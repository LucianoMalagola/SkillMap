"""
SkillMap: transform the latest raw files and load in-scope postings into PostgreSQL.

For each source (greenhouse, lever, ashby, jooble, adzuna, remotive):
  1. Read the latest data/raw/<source>/<date>/ (ATS: only boards marked "ok" in _manifest.json).
  2. Normalize each posting (etl/normalize.py): HTML cleanup, Lever plain-text fallback.
  3. Exclude, counting each posting under the FIRST reason it fails (in this order, so
     the counts read as a funnel):
       out_of_scope /     location rules (etl/location.py)
       ambiguous_location
       title_filter       not a target role (etl/roles.py)
       no_text            no description text at all
       placeholder        "TBD" or only legal/HR boilerplate (normalize.is_placeholder)
       duplicate          same company + title + description as another posting
                          (e.g. one copy per city); the smallest external_id is kept
  4. Detect modality, extract tools (etl/skills.py, synonyms from the DB).
  5. Upsert into jobs (ON CONFLICT (source, external_id)), replace job_skills.
  6. Deactivate stale jobs:
       - boards fetched OK today (and Remotive, when every category was fetched): their
         active jobs not loaded today
       - any source: jobs not seen for STALE_DAYS (covers Jooble, whose search results
         aren't a full inventory, and boards removed from companies.csv)
  7. Log one etl_runs row per source with exclusion counts by reason.

Each source loads in its own transaction: a failure rolls back that source only and
marks its etl_runs row 'failed'.

Usage:
    python etl/transform_load.py                     # all sources, latest raw day of each
    python etl/transform_load.py --sources lever ashby
    python etl/transform_load.py --date 2026-11-01
    python etl/transform_load.py --dry-run           # transform + report, no writes
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg
from dotenv import load_dotenv
from psycopg.types.json import Jsonb

sys.path.insert(0, str(Path(__file__).resolve().parent))
from location import classify_posting  # noqa: E402
from normalize import ATS_SOURCES, detect_modality, is_placeholder, normalize  # noqa: E402
from roles import classify_title  # noqa: E402
from skills import SkillExtractor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SOURCES = ("greenhouse", "lever", "ashby", "jooble", "adzuna", "remotive")
STALE_DAYS = 14
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---- read ----------------------------------------------------------------------------------

def latest_day_dir(source: str, day: str | None) -> Path | None:
    base = RAW_DIR / source
    if not base.is_dir():
        return None
    if day:
        candidate = base / day
        return candidate if candidate.is_dir() else None
    days = sorted(d for d in base.iterdir() if d.is_dir() and DAY_RE.match(d.name)
                  and (source not in ATS_SOURCES + ("remotive",) or (d / "_manifest.json").exists()))
    return days[-1] if days else None


def read_raw(source: str, day_dir: Path) -> tuple[list[tuple[str, dict]], list[str] | None, int, bool]:
    """Return ([(company, job), ...], companies fetched OK (ATS only), boards/categories failed,
    full_inventory: True when the whole source was fetched completely (Remotive))."""
    if source in ATS_SOURCES:
        manifest = json.loads((day_dir / "_manifest.json").read_text(encoding="utf-8"))
        items, ok_companies, failed = [], [], 0
        for board in manifest["boards"]:
            if board["status"] != "ok":
                failed += 1
                continue
            payload = json.loads((day_dir / board["file"]).read_text(encoding="utf-8"))
            for job in (payload if source == "lever" else payload["jobs"]):
                items.append((board["company"], job))
            ok_companies.append(board["company"])
        return items, ok_companies, failed, False

    if source == "remotive":
        # One file per category; each is that category's complete current list. The same
        # posting can appear in several categories.
        manifest = json.loads((day_dir / "_manifest.json").read_text(encoding="utf-8"))
        items, seen, failed = [], set(), 0
        for entry in manifest["categories"]:
            if entry["status"] != "ok":
                failed += 1
                continue
            for job in json.loads((day_dir / entry["file"]).read_text(encoding="utf-8"))["jobs"]:
                if str(job["id"]) not in seen:
                    seen.add(str(job["id"]))
                    items.append(("", job))
        return items, None, failed, failed == 0 and bool(manifest["categories"])

    # Search APIs (Jooble, Adzuna): one file per query; the same posting can come back in
    # several queries. Results aren't a full inventory, so no per-board deactivation.
    list_key = {"jooble": "jobs", "adzuna": "results"}[source]
    items, seen = [], set()
    for path in sorted(day_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        for job in json.loads(path.read_text(encoding="utf-8")).get(list_key, []):
            if str(job.get("id")) not in seen:
                seen.add(str(job.get("id")))
                items.append(("", job))
    return items, None, 0, False


# ---- transform -------------------------------------------------------------------------------

def transform(source: str, items: list[tuple[str, dict]], extractor: SkillExtractor) -> tuple[list[dict], Counter]:
    excluded: Counter = Counter()
    candidates: list[dict] = []
    for company, job in items:
        row = normalize(source, job, company)
        market, location_reason = classify_posting(source, job, description=row["description"])
        if market is None:
            excluded[location_reason] += 1
            continue
        role = classify_title(row["title"])
        if role is None:
            excluded["title_filter"] += 1
            continue
        if not row["description"]:
            excluded["no_text"] += 1
            continue
        # Snippet sources (Adzuna, Jooble) are short by nature: only the explicit "TBD" check.
        if is_placeholder(row["description"]) and (row["description_is_full"] or
                                                    row["description"].lower().startswith(("tbd", "tba"))):
            excluded["placeholder"] += 1
            continue
        row.update(market=market, location_reason=location_reason, role_category=role,
                   modality=detect_modality(market, row["workplace_type"], row["location_raw"] or "",
                                            row["title"], row["description"]),
                   skills=sorted(extractor.extract(f"{row['title']}\n{row['description']}")))
        candidates.append(row)

    # Duplicates: same company + title + description. Keep the smallest external_id so the
    # same copy survives from day to day.
    kept: dict[tuple, dict] = {}
    for row in sorted(candidates, key=lambda r: r["external_id"]):
        key = (row["company"], row["title"].lower(), hashlib.sha1(row["description"].encode("utf-8")).hexdigest())
        if key in kept:
            excluded["duplicate"] += 1
        else:
            kept[key] = row
    return list(kept.values()), excluded


# ---- load -------------------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO jobs (source, external_id, title, company, location_raw, market, modality, role_category,
                  salary_min, salary_max, salary_currency, description, description_is_full, url, posted_at)
VALUES (%(source)s, %(external_id)s, %(title)s, %(company)s, %(location_raw)s, %(market)s, %(modality)s,
        %(role_category)s, %(salary_min)s, %(salary_max)s, %(salary_currency)s, %(description)s,
        %(description_is_full)s, %(url)s, %(posted_at)s)
ON CONFLICT (source, external_id) DO UPDATE SET
    title = EXCLUDED.title, company = EXCLUDED.company, location_raw = EXCLUDED.location_raw,
    market = EXCLUDED.market, modality = EXCLUDED.modality, role_category = EXCLUDED.role_category,
    salary_min = EXCLUDED.salary_min, salary_max = EXCLUDED.salary_max,
    salary_currency = EXCLUDED.salary_currency, description = EXCLUDED.description,
    description_is_full = EXCLUDED.description_is_full, url = EXCLUDED.url, posted_at = EXCLUDED.posted_at,
    last_seen = now(), is_active = TRUE
RETURNING job_id, (xmax = 0) AS inserted
"""


def load(conn: psycopg.Connection, source: str, rows: list[dict], ok_companies: list[str] | None,
         skill_ids: dict[str, int], full_inventory: bool = False) -> dict:
    stats = Counter()
    with conn.transaction():
        with conn.cursor() as cur:
            for row in rows:
                job_id, inserted = cur.execute(UPSERT_SQL, row).fetchone()
                stats["new" if inserted else "updated"] += 1
                cur.execute("DELETE FROM job_skills WHERE job_id = %s", (job_id,))
                if row["skills"]:
                    cur.executemany("INSERT INTO job_skills (job_id, skill_id) VALUES (%s, %s)",
                                    [(job_id, skill_ids[s]) for s in row["skills"]])

            if ok_companies:   # ATS boards fetched OK today: anything not loaded today is gone
                loaded_ids = [r["external_id"] for r in rows]
                cur.execute("""UPDATE jobs SET is_active = FALSE
                               WHERE source = %s AND is_active AND company = ANY(%s)
                                 AND NOT (external_id = ANY(%s))""", (source, ok_companies, loaded_ids))
                stats["deactivated"] += cur.rowcount
            if full_inventory:  # whole source fetched completely (Remotive): same rule, source-wide
                cur.execute("""UPDATE jobs SET is_active = FALSE
                               WHERE source = %s AND is_active AND NOT (external_id = ANY(%s))""",
                            (source, [r["external_id"] for r in rows]))
                stats["deactivated"] += cur.rowcount
            cur.execute("""UPDATE jobs SET is_active = FALSE
                           WHERE source = %s AND is_active AND last_seen < now() - make_interval(days => %s)""",
                        (source, STALE_DAYS))
            stats["deactivated"] += cur.rowcount
    return stats


def start_run(conn, source: str) -> int:
    return conn.execute("INSERT INTO etl_runs (source) VALUES (%s) RETURNING run_id", (source,)).fetchone()[0]


def finish_run(conn, run_id: int, status: str, fetched: int, new: int, excluded: Counter, error: str | None = None):
    conn.execute("""UPDATE etl_runs SET finished_at = now(), status = %s, jobs_fetched = %s, jobs_new = %s,
                    jobs_excluded = %s, exclusion_reasons = %s, error_message = %s WHERE run_id = %s""",
                 (status, fetched, new, sum(excluded.values()), Jsonb(dict(excluded)), error, run_id))


# ---- main ----------------------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Transform raw postings and load them into Neon")
    parser.add_argument("--sources", nargs="+", choices=SOURCES, default=list(SOURCES))
    parser.add_argument("--date", help="raw day to process (default: latest per source)")
    parser.add_argument("--dry-run", action="store_true", help="transform and report, write nothing")
    args = parser.parse_args()

    env_path = ROOT / ".env"
    load_dotenv(env_path)
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print(f"ERROR: DATABASE_URL is not set (checked the environment and {env_path}).")
        return 1

    failures = 0
    with psycopg.connect(database_url, autocommit=True) as conn:
        synonyms = dict(conn.execute("""SELECT ss.synonym, s.skill_name FROM skill_synonyms ss
                                        JOIN skills s USING (skill_id)""").fetchall())
        skill_ids = dict(conn.execute("SELECT skill_name, skill_id FROM skills").fetchall())
        extractor = SkillExtractor(synonyms)

        for source in args.sources:
            day_dir = latest_day_dir(source, args.date)
            if day_dir is None:
                print(f"[{source}] no raw data found, skipped")
                continue
            items, ok_companies, failed_boards, full_inventory = read_raw(source, day_dir)
            rows, excluded = transform(source, items, extractor)
            boards = f", {len(ok_companies)} boards ok / {failed_boards} failed" if ok_companies is not None else ""
            print(f"[{source}] {day_dir.name}: {len(items)} postings{boards} -> {len(rows)} to load; "
                  f"excluded {sum(excluded.values())} {dict(excluded.most_common())}")
            if args.dry_run:
                continue

            run_id = start_run(conn, source)
            try:
                stats = load(conn, source, rows, ok_companies, skill_ids, full_inventory)
                finish_run(conn, run_id, "success", len(items), stats["new"], excluded)
                print(f"[{source}] loaded: {stats['new']} new, {stats['updated']} updated, "
                      f"{stats['deactivated']} deactivated (etl_runs.run_id={run_id})")
            except Exception as err:   # record the failure, keep going with the other sources
                failures += 1
                finish_run(conn, run_id, "failed", len(items), 0, excluded, f"{type(err).__name__}: {err}")
                print(f"[{source}] FAILED, rolled back: {type(err).__name__}: {err}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

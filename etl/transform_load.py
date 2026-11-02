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
       duplicate          extra copies of one job (same company + title + description,
                          e.g. one copy per country). Copies are merged BEFORE the location
                          step and their locations pooled (location.classify_group), so they
                          count as one posting.
  4. Detect modality, extract tools (etl/skills.py, synonyms from the DB).
  5. Upsert into jobs (ON CONFLICT (source, external_id)), replace job_skills.
  6. Remove rows that today's rules exclude: a posting still present in today's raw data
     but not loaded (placeholder, now out of scope, merged into another copy) shouldn't be
     in the table at all ("out-of-scope postings are never loaded"). Without this, an old
     row would linger in the 90-day views under its old classification.
  7. Deactivate stale jobs (closed postings stay in the table, inactive):
       - boards fetched OK today (and Remotive, when every category was fetched): their
         active jobs not loaded today
       - any source: jobs not seen for STALE_DAYS (covers Jooble, whose search results
         aren't a full inventory, and boards removed from companies.csv)
  8. Log one etl_runs row per source with exclusion counts by reason.

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
from location import classify_group, classify_posting  # noqa: E402
from normalize import ATS_SOURCES, detect_modality, is_placeholder, normalize, parse_board  # noqa: E402
from roles import ROLE_FAMILIES, classify_title  # noqa: E402
from skills import SkillExtractor  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
SOURCES = ("greenhouse", "lever", "ashby", "recruitee", "personio", "jooble", "adzuna", "remotive")
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
            for job in parse_board(source, (day_dir / board["file"]).read_bytes()):
                if source == "personio":
                    job["_board_token"] = board["board_token"]   # needed to build the posting URL
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

MAX_LOCATION_RAW = 1000   # merged copies can list dozens of cities


def _sort_key(external_id: str):
    """Numeric ids sort as numbers (Greenhouse, Adzuna); others as text."""
    return (0, int(external_id), "") if external_id.isdigit() else (1, 0, external_id)


def transform(source: str, items: list[tuple[str, dict]], extractor: SkillExtractor) -> tuple[list[dict], Counter]:
    """Raw postings -> rows to load, plus exclusion counts by reason.

    Identical copies of one job (same company + title + description, e.g. one copy per
    country) are merged FIRST and treated as one posting: their locations are pooled for
    the market decision (location.classify_group). Every copy is counted under the group's
    outcome; when a group is loaded, its extra copies count as 'duplicate'.
    """
    excluded: Counter = Counter()
    groups: dict[tuple, list[tuple[dict, dict]]] = {}
    for company, job in items:
        row = normalize(source, job, company)
        key = (row["company"], row["title"].lower(), hashlib.sha1(row["description"].encode("utf-8")).hexdigest())
        groups.setdefault(key, []).append((job, row))

    rows: list[dict] = []
    for copies in groups.values():
        copies.sort(key=lambda c: _sort_key(c[1]["external_id"]))
        n = len(copies)
        description = copies[0][1]["description"]
        market, location_reason = classify_group(source, [job for job, _ in copies], description)
        if market is None:
            excluded[location_reason] += n
            continue
        role = classify_title(copies[0][1]["title"])
        if role is None:
            excluded["title_filter"] += n
            continue
        if not description:
            excluded["no_text"] += n
            continue
        # Snippet sources (Adzuna, Jooble) are short by nature: only the explicit "TBD" check.
        if is_placeholder(description) and (copies[0][1]["description_is_full"] or
                                            description.lower().startswith(("tbd", "tba"))):
            excluded["placeholder"] += n
            continue
        if n > 1:
            excluded["duplicate"] += n - 1

        # Store the copy whose own location matches the group's market (e.g. the San José
        # copy of a CR group), so the URL points to a relevant page; ties -> smallest id.
        row = next((r for job, r in copies if n == 1 or classify_posting(source, job, description)[0] == market),
                   copies[0][1])
        locations = list(dict.fromkeys(r["location_raw"] for _, r in copies if r["location_raw"]))
        location_raw = " | ".join(locations)
        if len(location_raw) > MAX_LOCATION_RAW:
            location_raw = location_raw[:MAX_LOCATION_RAW].rsplit(" | ", 1)[0] + f" | (+{n} copies in total)"
        row = dict(row, location_raw=location_raw)
        row.update(market=market, location_reason=location_reason, role_category=role,
                   role_family=ROLE_FAMILIES[role], copies=n,
                   modality=detect_modality(market, row["workplace_type"], row["location_raw"] or "",
                                            row["title"], description),
                   skills=sorted(extractor.extract(f"{row['title']}\n{description}")))
        rows.append(row)
    return rows, excluded


# ---- load -------------------------------------------------------------------------------------

UPSERT_SQL = """
INSERT INTO jobs (source, external_id, title, company, location_raw, market, modality, role_category, role_family,
                  salary_min, salary_max, salary_currency, description, description_is_full, url, posted_at)
VALUES (%(source)s, %(external_id)s, %(title)s, %(company)s, %(location_raw)s, %(market)s, %(modality)s,
        %(role_category)s, %(role_family)s, %(salary_min)s, %(salary_max)s, %(salary_currency)s, %(description)s,
        %(description_is_full)s, %(url)s, %(posted_at)s)
ON CONFLICT (source, external_id) DO UPDATE SET
    title = EXCLUDED.title, company = EXCLUDED.company, location_raw = EXCLUDED.location_raw,
    market = EXCLUDED.market, modality = EXCLUDED.modality, role_category = EXCLUDED.role_category,
    role_family = EXCLUDED.role_family,
    salary_min = EXCLUDED.salary_min, salary_max = EXCLUDED.salary_max,
    salary_currency = EXCLUDED.salary_currency, description = EXCLUDED.description,
    description_is_full = EXCLUDED.description_is_full, url = EXCLUDED.url, posted_at = EXCLUDED.posted_at,
    last_seen = now(), is_active = TRUE
RETURNING job_id, (xmax = 0) AS inserted
"""


def load(conn: psycopg.Connection, source: str, rows: list[dict], ok_companies: list[str] | None,
         skill_ids: dict[str, int], full_inventory: bool = False, seen_ids: list[str] | None = None) -> dict:
    """seen_ids: every external_id present in today's raw data for this source."""
    stats = Counter()
    loaded_ids = [r["external_id"] for r in rows]
    with conn.transaction():
        with conn.cursor() as cur:
            if seen_ids:   # in today's raw data but excluded by today's rules -> remove (job_skills cascade)
                cur.execute("""DELETE FROM jobs WHERE source = %s AND external_id = ANY(%s)
                               AND NOT (external_id = ANY(%s))""", (source, seen_ids, loaded_ids))
                stats["removed"] += cur.rowcount
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


def finish_run(conn, run_id: int, status: str, fetched: int, new: int, excluded: Counter, error: str | None = None,
               loaded: int = 0):
    conn.execute("""UPDATE etl_runs SET finished_at = now(), status = %s, jobs_fetched = %s, jobs_new = %s,
                    jobs_loaded = %s, jobs_excluded = %s, exclusion_reasons = %s, error_message = %s
                    WHERE run_id = %s""",
                 (status, fetched, new, loaded, sum(excluded.values()), Jsonb(dict(excluded)), error, run_id))


def check_role_families(conn) -> None:
    """roles.ROLE_FAMILIES must match the role_families table (the jobs FK enforces the pair)."""
    db = dict(conn.execute("SELECT role_category, role_family FROM role_families").fetchall())
    if db != ROLE_FAMILIES:
        raise RuntimeError(f"etl/roles.py ROLE_FAMILIES and the role_families table differ: "
                           f"code={ROLE_FAMILIES} db={db}. Add a migration that updates role_families.")


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
        check_role_families(conn)

        for source in args.sources:
            day_dir = latest_day_dir(source, args.date)
            if day_dir is None:
                print(f"[{source}] no raw data found, skipped")
                continue
            items, ok_companies, failed_boards, full_inventory = read_raw(source, day_dir)
            rows, excluded = transform(source, items, extractor)
            seen_ids = list({normalize(source, job, company)["external_id"] for company, job in items})
            boards = f", {len(ok_companies)} boards ok / {failed_boards} failed" if ok_companies is not None else ""
            print(f"[{source}] {day_dir.name}: {len(items)} postings{boards} -> {len(rows)} to load; "
                  f"excluded {sum(excluded.values())} {dict(excluded.most_common())}")
            if args.dry_run:
                continue

            run_id = start_run(conn, source)
            try:
                stats = load(conn, source, rows, ok_companies, skill_ids, full_inventory, seen_ids)
                finish_run(conn, run_id, "success", len(items), stats["new"], excluded, loaded=len(rows))
                print(f"[{source}] loaded: {stats['new']} new, {stats['updated']} updated, "
                      f"{stats['removed']} removed (excluded by current rules), "
                      f"{stats['deactivated']} deactivated (etl_runs.run_id={run_id})")
            except Exception as err:   # record the failure, keep going with the other sources
                failures += 1
                finish_run(conn, run_id, "failed", len(items), 0, excluded, f"{type(err).__name__}: {err}")
                print(f"[{source}] FAILED, rolled back: {type(err).__name__}: {err}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

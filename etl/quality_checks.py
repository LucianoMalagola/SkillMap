"""
SkillMap: data-quality checks run at the end of every pipeline run. Any failed check makes
the run fail (exit 1), which fails the GitHub Actions job and triggers GitHub's failure email.

    python -m etl.quality_checks --target dev                      # checks on the data as it is
    python -m etl.quality_checks --target dev --since <timestamp>  # also checks one pipeline run

Checks on the whole database (always):
  no_duplicates        no (source, external_id) twice
  required_fields      no NULL title/company... in fields the dashboards rely on
  no_orphans           every job_skills row points at a job and a skill
  stale_deactivated    no posting still active after STALE_DAYS without being seen
  pct_in_range         every percentage in the views is between 0 and 100
  views_ok             every v_* view (and fn_skill_match) runs
  view_contract        every column in docs/view_contract.json still exists (dashboards depend on them)
  column_comments      every view column has a COMMENT (the dashboard label)

Checks on one pipeline run (with --since, which run_pipeline passes):
  runs_succeeded       every expected source logged a successful etl_runs row
  sources_nonempty     every expected source fetched at least one posting
  loaded_nonzero       the run loaded at least one posting in total
  no_unusual_drop      no source (or the total) loaded < DROP_RATIO of its previous run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import psycopg

try:
    from etl.db import TARGETS, database_url
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from etl.db import TARGETS, database_url

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_FILE = ROOT / "docs" / "view_contract.json"
STALE_DAYS = 14            # keep in step with transform_load.STALE_DAYS
DROP_RATIO = 0.5           # fail if a source loads less than half of what its previous run loaded...
DROP_MIN_PREVIOUS = 10     # ...and the previous run loaded at least this many (small numbers are noisy)

PCT_COLUMNS = {
    "v_skill_demand": ["pct_postings_90d"],
    "v_skill_demand_segment": ["pct_postings_90d", "mention_share_pct"],
    "v_skill_cooccurrence": ["pct_with_b_given_a_90d"],
    "v_weekly_skill_trend": ["pct_postings"],
    "v_modality_mix": ["pct_of_market_90d"],
}


class Result:
    def __init__(self, name: str, ok: bool, detail: str = ""):
        self.name, self.ok, self.detail = name, ok, detail

    def __str__(self):
        return f"{'PASS' if self.ok else 'FAIL'}  {self.name:<18} {self.detail}"


def _scalar(conn, sql: str, params=None):
    return conn.execute(sql, params).fetchone()[0]


def database_checks(conn: psycopg.Connection) -> list[Result]:
    out: list[Result] = []

    dups = conn.execute("""SELECT source, external_id, COUNT(*) FROM jobs
                           GROUP BY 1, 2 HAVING COUNT(*) > 1 LIMIT 5""").fetchall()
    out.append(Result("no_duplicates", not dups, f"{len(dups)} duplicated keys {dups}" if dups else "0"))

    nulls = conn.execute("""
        SELECT COUNT(*) FILTER (WHERE title IS NULL OR title = ''),
               COUNT(*) FILTER (WHERE market IS NULL),
               COUNT(*) FILTER (WHERE role_category IS NULL OR role_family IS NULL),
               COUNT(*) FILTER (WHERE modality IS NULL),
               COUNT(*) FILTER (WHERE description_is_full AND (description IS NULL OR description = '')),
               COUNT(*) FILTER (WHERE first_seen IS NULL OR last_seen IS NULL OR last_seen < first_seen)
        FROM jobs""").fetchone()
    labels = ("title", "market", "role", "modality", "full_text_without_text", "seen_dates")
    bad = {k: v for k, v in zip(labels, nulls) if v}
    out.append(Result("required_fields", not bad, str(bad) if bad else "0 problems"))

    orphans = _scalar(conn, """SELECT COUNT(*) FROM job_skills js
                               LEFT JOIN jobs j USING (job_id) LEFT JOIN skills s USING (skill_id)
                               WHERE j.job_id IS NULL OR s.skill_id IS NULL""")
    out.append(Result("no_orphans", orphans == 0, f"{orphans} orphan job_skills rows"))

    stale = _scalar(conn, """SELECT COUNT(*) FROM jobs WHERE is_active
                             AND last_seen < now() - make_interval(days => %s)""", (STALE_DAYS + 1,))
    out.append(Result("stale_deactivated", stale == 0, f"{stale} active postings not seen for >{STALE_DAYS} days"))

    views = [r[0] for r in conn.execute("""SELECT viewname FROM pg_views
                                           WHERE schemaname = 'public' AND viewname LIKE 'v\\_%' ORDER BY 1""")]
    broken = []
    for view in views + ["fn_skill_match"]:
        sql = ("SELECT COUNT(*) FROM fn_skill_match(ARRAY['SQL','Excel'])" if view == "fn_skill_match"
               else f'SELECT COUNT(*) FROM "{view}"')
        try:
            with conn.transaction():
                conn.execute(sql).fetchone()
        except psycopg.Error as err:
            broken.append(f"{view}: {type(err).__name__}")
    out.append(Result("views_ok", not broken, "; ".join(broken) or f"{len(views)} views + fn_skill_match"))

    bad_pct = []
    for view, cols in PCT_COLUMNS.items():
        for col in cols:
            n = _scalar(conn, f'SELECT COUNT(*) FROM "{view}" WHERE "{col}" < 0 OR "{col}" > 100 OR "{col}" IS NULL')
            if n:
                bad_pct.append(f"{view}.{col}: {n} rows")
    out.append(Result("pct_in_range", not bad_pct, "; ".join(bad_pct) or "all within 0-100"))

    live = {}
    for view, col, comment in conn.execute("""
            SELECT c.relname, a.attname, col_description(c.oid, a.attnum)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            WHERE n.nspname = 'public' AND c.relkind = 'v' AND c.relname LIKE 'v\\_%'"""):
        live.setdefault(view, {})[col] = comment
    if CONTRACT_FILE.exists():
        contract = json.loads(CONTRACT_FILE.read_text(encoding="utf-8"))["views"]
        missing = [f"{v}.{c}" for v, cols in contract.items() for c in cols if c not in live.get(v, {})]
        out.append(Result("view_contract", not missing,
                          "missing: " + ", ".join(missing) if missing else f"{len(contract)} views match"))
    else:
        out.append(Result("view_contract", False, f"{CONTRACT_FILE.name} not found"))
    uncommented = [f"{v}.{c}" for v, cols in live.items() for c, comment in cols.items() if not comment]
    out.append(Result("column_comments", not uncommented,
                      ", ".join(uncommented) if uncommented else "every view column has a label"))
    return out


def run_checks(conn: psycopg.Connection, since: datetime, expected_sources: list[str]) -> list[Result]:
    out: list[Result] = []
    runs = {r[0]: r[1:] for r in conn.execute("""
        SELECT DISTINCT ON (source) source, status, jobs_fetched, COALESCE(jobs_loaded, 0), error_message
        FROM etl_runs WHERE started_at >= %s ORDER BY source, started_at DESC""", (since,))}

    not_ok = [f"{s}: {runs[s][0] if s in runs else 'no run logged'}" for s in expected_sources
              if s not in runs or runs[s][0] != "success"]
    out.append(Result("runs_succeeded", not not_ok, "; ".join(not_ok) or ", ".join(expected_sources)))

    empty = [s for s in expected_sources if s in runs and not runs[s][1]]
    out.append(Result("sources_nonempty", not empty,
                      f"fetched nothing: {', '.join(empty)}" if empty else "every source fetched postings"))

    total = sum(r[2] for r in runs.values())
    out.append(Result("loaded_nonzero", total > 0, f"{total} postings loaded"))

    drops = []
    previous_total = 0
    for source in expected_sources:
        prev = conn.execute("""SELECT jobs_loaded FROM etl_runs
                               WHERE source = %s AND status = 'success' AND started_at < %s
                                 AND jobs_loaded IS NOT NULL
                               ORDER BY started_at DESC LIMIT 1""", (source, since)).fetchone()
        if not prev:
            continue
        previous_total += prev[0]
        now_loaded = runs.get(source, (None, None, 0))[2]
        if prev[0] >= DROP_MIN_PREVIOUS and now_loaded < DROP_RATIO * prev[0]:
            drops.append(f"{source}: {prev[0]} -> {now_loaded}")
    if previous_total >= DROP_MIN_PREVIOUS and total < DROP_RATIO * previous_total:
        drops.append(f"total: {previous_total} -> {total}")
    out.append(Result("no_unusual_drop", not drops,
                      "; ".join(drops) or f"no source below {DROP_RATIO:.0%} of its previous run"))
    return out


def report(results: list[Result], title: str, log=print) -> bool:
    log(f"\n== {title} ==")
    for r in results:
        log(str(r))
    failed = [r for r in results if not r.ok]
    log(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return not failed


def main() -> int:
    parser = argparse.ArgumentParser(description="SkillMap data-quality checks")
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--since", type=datetime.fromisoformat, help="start of the pipeline run to check")
    parser.add_argument("--sources", nargs="+", default=[], help="sources expected in that run")
    args = parser.parse_args()
    with psycopg.connect(database_url(args.target), autocommit=True) as conn:
        results = database_checks(conn)
        if args.since:
            results += run_checks(conn, args.since, args.sources)
    return 0 if report(results, f"data-quality checks ({args.target})") else 1


if __name__ == "__main__":
    sys.exit(main())

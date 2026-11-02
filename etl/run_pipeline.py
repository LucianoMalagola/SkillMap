"""
SkillMap: run the whole pipeline. The one command used locally AND by GitHub Actions.

    python -m etl.run_pipeline --target dev                     # extract everything, load, check
    python -m etl.run_pipeline --target dev --skip-jooble       # while Jooble's key is US-only
    python -m etl.run_pipeline --target dev --skip-extract      # reuse today's raw files (no API calls)
    python -m etl.run_pipeline --target dev --skip-extract --date 2026-11-01   # reprocess a past day
    python -m etl.run_pipeline --target dev --sources lever     # a single source
    python -m etl.run_pipeline --target dev --skip theirstack   # everything except some sources
    python -m etl.run_pipeline --target dev --dry-run           # transform + report, write nothing
    python -m etl.run_pipeline --target main --migrate          # what the workflow runs

Steps:
  1. --migrate: apply pending migrations (etl/migrate.py). The workflow always passes it, so
     main gets schema changes only through the workflow, after they've been tested on dev.
  2. Extract (unless --skip-extract): extract_ats / extract_adzuna / extract_remotive /
     extract_jooble / extract_theirstack for the selected sources. A failing extractor doesn't
     stop the others; the run is marked failed at the end.
     TheirStack is metered (credits shared by dev, main and local runs): it gets
     --since <last successful TheirStack run on the target, minus 1 h> and a per-run cap
     (THEIRSTACK_MAX_CREDITS: small on dev, enough for the 90-day backfill on main;
     --theirstack-max-credits overrides). The extractor also never exceeds the month's
     credits; hitting a cap is logged, not a failure.
  3. Scrub: redact any API key found in data/raw/ (safety net before the artifact upload).
  4. Transform + load (etl/transform_load.py) for the selected sources, always for ONE raw
     day (today, or --date). Never "latest available": reloading an old day would mark
     closed postings as seen today.
  5. Data-quality checks (etl/quality_checks.py) on the database and on this run.
     Any failure -> exit code 1 (fails the GitHub job, which sends the failure email).

Target database: see etl/db.py (dev: DATABASE_URL_DEV or DATABASE_URL; main: DATABASE_URL_MAIN).
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg

try:
    from etl import migrate, quality_checks
    from etl.db import TARGETS, database_url
    from etl.redact import SECRET_ENV_VARS, scrub_tree
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from etl import migrate, quality_checks
    from etl.db import TARGETS, database_url
    from etl.redact import SECRET_ENV_VARS, scrub_tree

ROOT = Path(__file__).resolve().parent.parent
ETL = ROOT / "etl"
RAW_DIR = ROOT / "data" / "raw"
COMPANIES = ETL / "companies.csv"
ATS_SOURCES = ("greenhouse", "lever", "ashby", "recruitee", "personio")
OTHER_SOURCES = ("adzuna", "remotive", "jooble", "theirstack")
ALL_SOURCES = ATS_SOURCES + OTHER_SOURCES
# Per-run TheirStack credit caps. The free plan has 200 credits a month for ALL runs, so dev
# test runs get a small slice and main gets enough for its first 90-day backfill (~140).
THEIRSTACK_MAX_CREDITS = {"dev": 15, "main": 150}
SINCE_OVERLAP = timedelta(hours=1)


def log(msg: str = "") -> None:
    print(msg, flush=True)


def ats_with_boards(path: Path = COMPANIES) -> set[str]:
    """ATS platforms that have at least one board in companies.csv (others have nothing to fetch)."""
    with path.open(newline="", encoding="utf-8") as f:
        return {row["ats"] for row in csv.DictReader(f)}


def select_sources(requested: list[str] | None, skip: list[str] | None, boards: set[str]) -> list[str]:
    sources = list(requested) if requested else [s for s in ALL_SOURCES if s not in ATS_SOURCES or s in boards]
    return [s for s in sources if s not in (skip or [])]


def extract_commands(sources: list[str], theirstack_args: list[str] | None = None) -> list[tuple[str, list[str]]]:
    """(label, argv) per extractor; all ATS platforms go in one extract_ats call."""
    py = sys.executable
    cmds = []
    ats = [s for s in sources if s in ATS_SOURCES]
    if ats:
        cmds.append(("ats", [py, str(ETL / "extract_ats.py"), "--ats", *ats]))
    for source in OTHER_SOURCES:
        if source in sources:
            extra = (theirstack_args or []) if source == "theirstack" else []
            cmds.append((source, [py, str(ETL / f"extract_{source}.py"), *extra]))
    return cmds


def theirstack_since(conn) -> datetime | None:
    """Start of the last successful TheirStack run that fetched something, minus an overlap;
    None = never ran on this database -> backfill."""
    last = conn.execute("""SELECT max(started_at) FROM etl_runs
                           WHERE source = 'theirstack' AND status = 'success' AND jobs_fetched > 0""").fetchone()[0]
    return last - SINCE_OVERLAP if last else None


def run(argv: list[str], env: dict | None = None) -> int:
    return subprocess.run(argv, cwd=ROOT, env=env, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the SkillMap pipeline end to end")
    parser.add_argument("--target", choices=TARGETS, required=True, help="database to load (dev or main)")
    parser.add_argument("--sources", nargs="+", choices=ALL_SOURCES, help="only these sources (default: all)")
    parser.add_argument("--skip", nargs="+", choices=ALL_SOURCES, default=[], help="leave these sources out")
    parser.add_argument("--skip-jooble", action="store_true", help="same as --skip jooble (its key is US-only for now)")
    parser.add_argument("--theirstack-max-credits", type=int,
                        help=f"TheirStack per-run credit cap (default per target: {THEIRSTACK_MAX_CREDITS})")
    parser.add_argument("--skip-extract", action="store_true", help="no API calls: reuse the raw files of --date")
    parser.add_argument("--date", help="raw day to transform, YYYY-MM-DD (default: today; implies --skip-extract)")
    parser.add_argument("--dry-run", action="store_true", help="transform and report only; no DB writes, no checks")
    parser.add_argument("--migrate", action="store_true", help="apply pending migrations first")
    parser.add_argument("--migrate-only", action="store_true", help="apply pending migrations and stop")
    parser.add_argument("--no-checks", action="store_true", help="skip the data-quality checks")
    args = parser.parse_args(argv)

    day = args.date or date.today().isoformat()
    skip_extract = args.skip_extract or bool(args.date)
    sources = select_sources(args.sources, args.skip + (["jooble"] if args.skip_jooble else []), ats_with_boards())
    failures: list[str] = []
    started = time.monotonic()

    try:
        url = database_url(args.target)
    except RuntimeError as err:
        log(f"ERROR: {err}")
        return 1
    log(f"SkillMap pipeline -> {args.target} | day {day} | sources: {', '.join(sources)}"
        f"{' | dry run' if args.dry_run else ''}")

    # 1. migrations
    if args.migrate or args.migrate_only:
        if args.dry_run:
            migrate.migrate(args.target, dry_run=True, log=log)
        else:
            try:
                migrate.migrate(args.target, log=log)
            except (psycopg.Error, RuntimeError) as err:
                log(f"ERROR: migration failed: {type(err).__name__}: {err}")
                return 1
        if args.migrate_only:
            return 0

    with psycopg.connect(url, autocommit=True) as conn:
        run_start = conn.execute("SELECT now()").fetchone()[0]   # DB clock: etl_runs uses it too
        since = theirstack_since(conn) if "theirstack" in sources else None
    theirstack_args = ["--max-credits", str(args.theirstack_max_credits if args.theirstack_max_credits is not None
                                            else THEIRSTACK_MAX_CREDITS[args.target])]
    if since:
        theirstack_args += ["--since", since.isoformat(timespec="seconds")]

    # 2. extract
    if not skip_extract:
        for label, cmd in extract_commands(sources, theirstack_args):
            log(f"\n== extract: {label} ==")
            code = run(cmd)
            if code:
                failures.append(f"extract {label} (exit {code})")

    # 3. scrub raw files (never let a key reach the artifact)
    if RAW_DIR.exists():
        scanned, fixed = scrub_tree(RAW_DIR, SECRET_ENV_VARS)
        log(f"\n== scrub: {scanned} raw files scanned, {fixed} had a credential redacted ==")

    # 4. transform + load
    log(f"\n== transform + load ({day}) ==")
    env = {**os.environ, "DATABASE_URL": url}
    cmd = [sys.executable, str(ETL / "transform_load.py"), "--sources", *sources, "--date", day]
    if args.dry_run:
        cmd.append("--dry-run")
    if not skip_extract:
        cmd.append("--log-api-units")
    code = run(cmd, env=env)
    if code:
        failures.append(f"transform_load (exit {code})")

    # 5. checks
    if not args.dry_run and not args.no_checks:
        with psycopg.connect(url, autocommit=True) as conn:
            results = quality_checks.database_checks(conn) + quality_checks.run_checks(conn, run_start, sources)
        if not quality_checks.report(results, f"data-quality checks ({args.target})", log=log):
            failures.append("data-quality checks")
        summary = os.getenv("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as f:
                f.write(f"### SkillMap {args.target}, {day}\n\n| Check | Result | Detail |\n|---|---|---|\n")
                f.writelines(f"| {r.name} | {'✅' if r.ok else '❌'} | {r.detail} |\n" for r in results)

    log(f"\nFinished in {time.monotonic() - started:.0f} s: "
        + ("OK" if not failures else "FAILED: " + "; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

"""
SkillMap: apply database migrations, tracked in a schema_migrations table.

    python -m etl.migrate --target dev              # apply pending migrations to dev
    python -m etl.migrate --target dev --dry-run    # list what would be applied
    python -m etl.migrate --target main             # the GitHub Actions workflow does this

Rules (also in CLAUDE.md):
  * Versioned migrations: sql/NN_<name>.sql, applied once, in order, each in its own
    transaction together with its schema_migrations row (all or nothing). Never edit one
    that has been applied to main; add a new file instead. They are written to be
    re-runnable anyway (IF NOT EXISTS, CREATE OR REPLACE, ON CONFLICT).
  * Repeatable scripts: sql/R_<name>.sql, run after every migrate (e.g. the read-only
    dashboard role and its grants, which a DROP VIEW in a later migration would lose).
  * Always dev first; main only through the workflow (etl/db.py has no local fallback
    for main).

Baseline: schema_migrations was introduced after migrations 01-07 had already been applied
by hand (dev: 01-07, main: 01-02). The first time the runner meets a database without the
table, it probes for each legacy migration's effect and records the ones present as
'baseline' instead of re-running them.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

import psycopg

try:
    from etl.db import TARGETS, database_url
except ImportError:  # run as a plain script: python etl/migrate.py
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from etl.db import TARGETS, database_url

ROOT = Path(__file__).resolve().parent.parent
SQL_DIR = ROOT / "sql"
VERSIONED_RE = re.compile(r"^(\d{2})_[\w-]+\.sql$")
REPEATABLE_RE = re.compile(r"^R_[\w-]+\.sql$")

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    method      TEXT NOT NULL CHECK (method IN ('applied', 'baseline'))
)"""

# Evidence that a legacy (pre-runner) migration has been applied. Checked in order; the
# first one that is absent and everything after it are applied normally.
LEGACY_PROBES = {
    "01": "SELECT to_regclass('public.jobs') IS NOT NULL",
    "02": "SELECT EXISTS (SELECT 1 FROM skills WHERE skill_name = 'Excel')",
    "03": """SELECT EXISTS (SELECT 1 FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'jobs' AND column_name = 'market')""",
    "04": "SELECT EXISTS (SELECT 1 FROM skills WHERE skill_name = 'Google Analytics')",
    "05": """SELECT to_regclass('public.v_skill_demand') IS NOT NULL
             AND pg_get_viewdef('public.v_skill_demand'::regclass) LIKE '%description_is_full%'""",
    "06": """SELECT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'jobs_source_check'
             AND pg_get_constraintdef(oid) LIKE '%recruitee%')""",
    "07": "SELECT to_regclass('public.v_posting_counts') IS NOT NULL",
}


def checksum(path: Path) -> str:
    # normalise line endings so a Windows checkout and the Linux runner agree
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:16]


def versioned_files() -> list[tuple[str, Path]]:
    files = [(m.group(1), p) for p in sorted(SQL_DIR.iterdir()) if (m := VERSIONED_RE.match(p.name))]
    versions = [v for v, _ in files]
    if len(versions) != len(set(versions)):
        raise RuntimeError(f"duplicate migration numbers in {SQL_DIR}: {versions}")
    return files


def repeatable_files() -> list[Path]:
    return [p for p in sorted(SQL_DIR.iterdir()) if REPEATABLE_RE.match(p.name)]


def _baseline(conn: psycopg.Connection, files: list[tuple[str, Path]]) -> list[str]:
    """Record legacy migrations whose effects are already present. Returns their versions."""
    present: list[str] = []
    for version, _ in files:
        probe = LEGACY_PROBES.get(version)
        if probe is None or not conn.execute(probe).fetchone()[0]:
            break
        present.append(version)
    # A later legacy migration present after a missing one means the DB was changed by hand
    # out of order: stop rather than guess.
    for version in [v for v, _ in files if v in LEGACY_PROBES and v not in present]:
        if version > (present[-1] if present else "00") and conn.execute(LEGACY_PROBES[version]).fetchone()[0]:
            raise RuntimeError(f"legacy migration {version} looks applied but an earlier one is missing; "
                               "fix schema_migrations by hand")
    with conn.transaction():
        for version, path in files:
            if version in present:
                conn.execute("INSERT INTO schema_migrations (version, filename, checksum, method) "
                             "VALUES (%s, %s, %s, 'baseline')", (version, path.name, checksum(path)))
    return present


def migrate(target: str, dry_run: bool = False, log=print) -> int:
    """Apply pending migrations (and the repeatable scripts). Returns how many were applied."""
    files = versioned_files()
    with psycopg.connect(database_url(target), autocommit=True) as conn:
        existed = conn.execute("SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]
        if not existed:
            if dry_run:
                log(f"[migrate:{target}] schema_migrations missing; a real run would baseline legacy migrations")
                present = [v for v, _ in files if v in LEGACY_PROBES
                           and conn.execute(LEGACY_PROBES[v]).fetchone()[0]]
                applied = {v: None for v in present}
            else:
                conn.execute(CREATE_TABLE)
                present = _baseline(conn, files)
                log(f"[migrate:{target}] created schema_migrations; baseline: {', '.join(present) or 'none'}")
                applied = None
        else:
            applied = None
        if applied is None:
            applied = dict(conn.execute("SELECT version, checksum FROM schema_migrations").fetchall())

        for version, path in files:
            if version in applied and applied[version] and applied[version] != checksum(path):
                log(f"[migrate:{target}] WARNING: {path.name} changed after it was applied "
                    "(add a new migration instead of editing an applied one)")

        pending = [(v, p) for v, p in files if v not in applied]
        if dry_run:
            log(f"[migrate:{target}] pending: {', '.join(p.name for _, p in pending) or 'none'}")
            return 0
        for version, path in pending:
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations (version, filename, checksum, method) "
                             "VALUES (%s, %s, %s, 'applied')", (version, path.name, checksum(path)))
            log(f"[migrate:{target}] applied {path.name}")
        for path in repeatable_files():
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
            log(f"[migrate:{target}] ran repeatable {path.name}")
        if not pending:
            log(f"[migrate:{target}] schema up to date ({max(applied) if applied else 'empty'})")
        return len(pending)


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply SkillMap database migrations")
    parser.add_argument("--target", choices=TARGETS, required=True)
    parser.add_argument("--dry-run", action="store_true", help="list pending migrations, change nothing")
    args = parser.parse_args()
    try:
        migrate(args.target, args.dry_run)
    except (psycopg.Error, RuntimeError) as err:
        print(f"[migrate:{args.target}] FAILED: {type(err).__name__}: {err}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

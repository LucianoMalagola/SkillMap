"""
SkillMap: generate docs/DATA_DICTIONARY.md and docs/view_contract.json from the database.

The view and column COMMENTs (written in the migrations) are the single source of the
labels, so the dictionary can't drift from the schema. view_contract.json lists every view
column the dashboards may rely on; quality_checks.py fails a run if one disappears.

    python -m etl.gen_data_dictionary --target dev

Refuses to remove a column from the contract (a breaking change for the dashboards) unless
--allow-breaking is given. Adding views or columns is always fine.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import psycopg

try:
    from etl.db import TARGETS, database_url
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from etl.db import TARGETS, database_url

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "DATA_DICTIONARY.md"
CONTRACT = ROOT / "docs" / "view_contract.json"

INTRO = """# SkillMap data dictionary

The **views are the contract with the dashboards** (Data Studio, Power BI). Columns are only
ever added; renaming or removing one is a breaking change and is announced first.
`docs/view_contract.json` lists the contracted columns and every pipeline run checks they still
exist (`etl/quality_checks.py`, check `view_contract`).

This file is generated from the database comments: `python -m etl.gen_data_dictionary --target dev`.
Edit labels in a migration (`COMMENT ON ...`), not here. The text before "## Views" is kept in
`etl/gen_data_dictionary.py`.

## Conventions

| Term | Meaning |
|---|---|
| **Last 90 days** (`*_90d`, `seen_90d`) | Postings seen at least once in the last 90 days (`jobs.last_seen >= now() - 90 days`), including ones that have since closed. |
| **Open now** (`*_open_now`, `open_now`) | Still listed as of the latest pipeline run (`jobs.is_active`). |
| **Full description** | `description_is_full = TRUE`: ATS boards and Remotive. Tool views count only these, in numerator **and** denominator. Adzuna/Jooble snippets count only in volume views. |
| **n / enough_data** | `n` = postings in the denominator of the row's percentage. `enough_data = n >= 10`. Dashboards show a percentage only when `enough_data` is true; otherwise show the count or "not enough data". |
| **Market** | `CR` (located specifically in Costa Rica: on-site, hybrid, or remote restricted to Costa Rica), `US-remote` (remote, open to US-based workers), `LATAM-remote` (remote, open to candidates in Costa Rica, among other countries, including places outside Latin America). Known limit: a remote posting listing 2+ Latin American countries counts as `LATAM-remote` even if Costa Rica isn't one of them; the rule treats it as open to the region. |
| **Available to CR** (`market = 'Available to CR'`, `is_rollup = TRUE`) | A grouping, not a fourth market: `CR` + `LATAM-remote`, each posting once, with its own percentages and `n`. **Dashboards must filter `is_rollup = FALSE` by default**, and select the grouping on purpose: its rows repeat postings already in `CR` and `LATAM-remote`, so summing across markets without the filter double-counts them. `v_postings` and `v_posting_skills` have no such rows, only an `available_to_cr` flag. |
| **Role family** | `Data & Analytics` (Data Analyst, BI Analyst, Data Reporting, Analytics & Insights), `Marketing & Insights` (Marketing Analyst, incl. Marketing Science), `Business & Operations` (Business Operations Analyst, Business Analyst). See `v_roles`. |
| **Weeks** | ISO weeks starting Monday, UTC. |

Personal data: no view exposes the description text (it can contain recruiter names/emails).
The `dashboard_reader` role can read the views below and nothing else.

## Skill match ("How well do my skills match?")

**Definition.** For a segment (role or role family × market), *match %* = share of all tool
requests in the segment's full-description postings (last 90 days) that your tools cover:
`SUM(mentions of your tools) / SUM(mentions of all tools)`. *What to learn next* = the
most-requested tools you don't have (highest `postings_mentioning_90d`).

**SQL / notebooks:**
```sql
SELECT * FROM fn_skill_match(ARRAY['Excel', 'SQL', 'Power BI'], 'role_category');  -- or 'role_family'
```

**Power BI** (import `v_skill_demand_segment` and `v_skills`; keep them **unrelated**: `v_skills`
is a disconnected "My skills" slicer table):
```DAX
-- Page filter: v_skill_demand_segment[grain] = "role_category" (or "role_family"), and either
-- is_rollup = FALSE or a single selected market (one market's rows never double-count)

Match % =
VAR mine   = VALUES ( v_skills[skill_name] )
VAR total  = CALCULATE ( SUM ( v_skill_demand_segment[postings_mentioning_90d] ) )
VAR hit    = CALCULATE ( SUM ( v_skill_demand_segment[postings_mentioning_90d] ),
                         TREATAS ( mine, v_skill_demand_segment[skill_name] ) )
RETURN IF ( ISFILTERED ( v_skills[skill_name] ), DIVIDE ( hit, total ) )

Is Missing =      -- use as a visual-level filter (= 1) on a table of skill_name, ranked by pct_postings_90d
IF ( ISFILTERED ( v_skills[skill_name] ),
     IF ( SELECTEDVALUE ( v_skill_demand_segment[skill_name] ) IN VALUES ( v_skills[skill_name] ), 0, 1 ),
     1 )

Enough Data = IF ( MIN ( v_skill_demand_segment[n] ) >= 10, "yes", "not enough data" )
```
Put `segment` and `market` on rows of a matrix with `Match %`, and always filter `grain` to one value.
`fn_skill_match` returns one row per segment and market: `market = 'Available to CR'` (`is_rollup = TRUE`)
is the match against CR + LATAM-remote postings together. Read it on its own; don't add it to the others.

**Data Studio:** no parameters for PostgreSQL custom queries, so use a fixed skill list in a
custom query (`SELECT * FROM fn_skill_match(ARRAY['Excel','SQL'])`) or chart
`v_skill_demand_segment` directly.
"""


def fetch(conn) -> tuple[dict, dict, str]:
    views: dict[str, dict] = {}
    for name, comment in conn.execute("""
            SELECT c.relname, obj_description(c.oid, 'pg_class')
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'v' AND c.relname LIKE 'v\\_%' ORDER BY 1"""):
        views[name] = {"comment": comment or "", "columns": []}
    for view, col, typ, comment in conn.execute("""
            SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod), col_description(c.oid, a.attnum)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
            WHERE n.nspname = 'public' AND c.relkind = 'v' AND c.relname LIKE 'v\\_%'
            ORDER BY c.relname, a.attnum"""):
        views[view]["columns"].append((col, typ, comment or ""))
    fn = conn.execute("""SELECT pg_get_function_arguments(p.oid), pg_get_function_result(p.oid),
                                obj_description(p.oid, 'pg_proc')
                         FROM pg_proc p WHERE p.proname = 'fn_skill_match'""").fetchone()
    fn_doc = f"`fn_skill_match({fn[0]})` returns `{fn[1]}`.\n\n{fn[2] or ''}\n" if fn else ""
    return views, {v: [c for c, _, _ in d["columns"]] for v, d in views.items()}, fn_doc


def render(views: dict, fn_doc: str) -> str:
    parts = [INTRO, "## Views\n"]
    parts.append("| View | Purpose |\n|---|---|")
    parts += [f"| [`{v}`](#{v}) | {d['comment']} |" for v, d in views.items()]
    parts.append("")
    for v, d in views.items():
        parts.append(f"### {v}\n\n{d['comment']}\n\n| Column | Type | Label / meaning |\n|---|---|---|")
        for col, typ, comment in d["columns"]:
            parts.append(f"| `{col}` | {typ} | {comment.replace('|', '/')} |")
        parts.append("")
    if fn_doc:
        parts.append("## Functions\n\n" + fn_doc)
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the data dictionary from DB comments")
    parser.add_argument("--target", choices=TARGETS, default="dev")
    parser.add_argument("--allow-breaking", action="store_true",
                        help="accept columns disappearing from the contract (tell the dashboard owner!)")
    args = parser.parse_args()
    with psycopg.connect(database_url(args.target), autocommit=True) as conn:
        views, contract, fn_doc = fetch(conn)

    if CONTRACT.exists() and not args.allow_breaking:
        old = json.loads(CONTRACT.read_text(encoding="utf-8"))["views"]
        removed = [f"{v}.{c}" for v, cols in old.items() for c in cols if c not in contract.get(v, [])]
        if removed:
            print("REFUSING: these contracted columns no longer exist: " + ", ".join(removed))
            print("That breaks the dashboards. Restore them, or re-run with --allow-breaking after telling the owner.")
            return 1

    DOC.write_text(render(views, fn_doc), encoding="utf-8")
    CONTRACT.write_text(json.dumps({"note": "Generated by etl/gen_data_dictionary.py. Columns dashboards rely on; "
                                            "never rename or remove without notice.",
                                    "views": contract}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {DOC.relative_to(ROOT)} and {CONTRACT.relative_to(ROOT)}: {len(views)} views, "
          f"{sum(len(c) for c in contract.values())} columns")
    return 0


if __name__ == "__main__":
    sys.exit(main())

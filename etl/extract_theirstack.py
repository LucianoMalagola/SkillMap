"""
SkillMap: extract Costa Rica and LATAM-remote postings for our target roles from the
TheirStack Jobs API (https://theirstack.com/en/docs/api-reference/jobs/search_jobs_v1).

Saves every response body (key-free) to data/raw/theirstack/<date>/<query>_p<page>.json plus
a _manifest.json (status per query, credits before/after, cap), so the transform step can
be re-run without spending credits.

Compliance (docs/SOURCES.md): documented key-based API; its terms allow public display of
postings and of aggregates. We only request postings that have a final_url (the
employer's own career site or ATS page), not LinkedIn/Indeed-only listings.

Credits (free plan): 200 API credits a month, 1 credit per job RETURNED; max 5 pages x 25
results per search; 10 requests a minute. Hard caps, enforced before every request:
  * monthly: TheirStack's own counter (GET /v0/billing/credit-balance, free) is the source
    of truth, shared by every run (dev, main, laptop). A run never lets the month's usage
    go above api_credits - RESERVE_CREDITS (kept for manual checks).
  * per run: --max-credits (run_pipeline passes a small cap for dev, a larger one for main).
  * per query: QUERY_MAX_CREDITS, so a broad query can't use up what the others need.
Reaching a cap is NOT an error: it is logged, recorded in the manifest, and the run goes on
(exit 0). Nothing is lost: results come oldest first, and each query's cursor (manifest
"cursors", stored in etl_runs.source_cursor once loaded) only moves as far as what was fetched:
  * query finished          -> this run's start, minus an hour of overlap
  * stopped early           -> discovered_at of the last posting fetched
  * stopped, nothing fetched -> unchanged (no cursor yet = backfill next time)

Incremental: --since asks only for postings TheirStack discovered at/after a timestamp, one for
all queries or per query (run_pipeline passes the stored cursors). Without it: the last 90 days.

Usage:
    python etl/extract_theirstack.py --max-credits 15
    python etl/extract_theirstack.py --since 2026-11-02T10:00:00Z --max-credits 150
    python etl/extract_theirstack.py --since cr=2026-11-02T10:00:00Z latam_remote=2026-11-01T08:00:00Z
    python etl/extract_theirstack.py --plan          # show the balance and the cap, spend nothing
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact

API = "https://api.theirstack.com"
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "theirstack"

LOOKBACK_DAYS = 90          # same window as the *_90d views
PAGE_SIZE = 25              # free plan: max 25 results per page...
MAX_PAGES = 5               # ...and 5 pages per search
REQUEST_DELAY = 7           # seconds; free plan allows 10 requests a minute
RESERVE_CREDITS = 10        # never used by the pipeline: left for manual checks
DEFAULT_MAX_CREDITS = 150   # per run
# The LATAM region filter's reach is unmeasured (it may match remote jobs in one member country,
# which we exclude): keep it from using the credits CR needs. Revisit after a few real runs.
QUERY_MAX_CREDITS = {"latam_remote": 3}
CURSOR_OVERLAP = timedelta(hours=1)
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"   # the format in TheirStack's docs

# Pre-filter on titles, so credits are spent on likely target roles; etl/roles.py decides.
TITLE_PATTERNS = [
    r"\bdata analyst", r"analista de datos", r"\b(bi|business intelligence) analyst",
    r"analista (de )?(bi|inteligencia de negocios)", r"reporting analyst", r"analista de reportes",
    r"\b(insights|analytics) analyst", r"marketing (science )?analyst", r"analista de marketing",
    r"business (operations|ops) analyst", r"business analyst", r"analista de negocios", r"data reporting",
]
LATAM_REGION_ID = 99000012   # "LATAM" in GET /v0/catalog/locations (free)

QUERIES = {
    "cr": {"job_country_code_or": ["CR"]},
    # remote postings open to Latin America; CR is covered above, US-remote by other sources
    "latam_remote": {"workplace_types_or": ["remote"], "job_location_or": [{"id": LATAM_REGION_ID}],
                     "job_country_code_not": ["US", "CR"]},
}


def build_body(query: str, page: int, limit: int, since: str | None) -> dict:
    body = {
        **QUERIES[query],
        "job_title_pattern_or": TITLE_PATTERNS,
        "posted_at_max_age_days": LOOKBACK_DAYS,
        "property_exists_or": ["final_url"],           # employer career site / ATS page only
        "order_by": [{"field": "discovered_at", "desc": False}],   # oldest first: the cursor depends on it
        "page": page,
        "limit": limit,
    }
    if since:
        body["discovered_at_gte"] = since
    return body


def parse_since(values: list[str] | None, queries: list[str]) -> dict[str, str]:
    """--since 2026-11-02T10:00:00Z (all queries) or cr=... latam_remote=... -> {query: timestamp}."""
    since: dict[str, str] = {}
    for value in values or []:
        query, separator, timestamp = value.partition("=")
        since.update({query: timestamp} if separator else dict.fromkeys(queries, value))
    return since


def discovered_times(jobs: list[dict]) -> list[datetime]:
    return [datetime.fromisoformat(j["discovered_at"]) for j in jobs if j.get("discovered_at")]


def next_cursor(finished: bool, discovered: list[datetime], started: datetime, since: str | None) -> str | None:
    """Where the next run starts for one query (see the module docstring).
    shortcut: truncated to the second and inclusive, so a stopped query re-buys its last posting (1 credit);
    more postings in one second than a run can fetch would never advance. Not seen; use `cursor` paging if it is."""
    if finished:
        return (started - CURSOR_OVERLAP).strftime(TIME_FORMAT)
    return discovered[-1].strftime(TIME_FORMAT) if discovered else since


def credits_allowed(balance: dict, max_credits: int, reserve: int = RESERVE_CREDITS) -> int:
    """Credits this run may spend: the smaller of the per-run cap and what is left of the
    month's credits after the reserve. Never negative."""
    left = int(balance.get("api_credits") or 0) - int(balance.get("used_api_credits") or 0) - reserve
    return max(0, min(max_credits, left))


def get_balance(session: requests.Session) -> dict:
    response = session.get(f"{API}/v0/billing/credit-balance", timeout=30)
    response.raise_for_status()
    return response.json()


def post_search(session: requests.Session, body: dict) -> requests.Response:
    """POST with up to 3 attempts on network errors, 429 and 5xx."""
    for attempt in range(3):
        try:
            response = session.post(f"{API}/v1/jobs/search", json=body, timeout=120)
        except requests.RequestException:
            if attempt == 2:
                raise
        else:
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt == 2:
                return response
        time.sleep(30 * (attempt + 1))
    raise RuntimeError("unreachable")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract TheirStack postings (CR + LATAM-remote)")
    parser.add_argument("--since", nargs="+", metavar="[QUERY=]TIMESTAMP",
                        help="only postings discovered at/after this ISO timestamp (default: 90-day backfill)")
    parser.add_argument("--max-credits", type=int, default=DEFAULT_MAX_CREDITS, help="per-run credit cap")
    parser.add_argument("--queries", nargs="+", choices=list(QUERIES), default=list(QUERIES))
    parser.add_argument("--plan", action="store_true", help="show balance and cap only; spend nothing")
    args = parser.parse_args(argv)
    since = parse_since(args.since, args.queries)

    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    key = os.getenv("THEIRSTACK_API_KEY", "").strip()
    if not key:
        print("ERROR: THEIRSTACK_API_KEY is not set (environment or .env).")
        return 1

    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                            "User-Agent": "SkillMap job-market research (portfolio project)"})
    try:
        before = get_balance(session)
    except (requests.RequestException, ValueError) as err:
        status = getattr(getattr(err, "response", None), "status_code", None)
        print(f"ERROR: could not read the TheirStack credit balance ({type(err).__name__}, HTTP {status}); "
              "nothing requested.")
        return 1
    allowed = credits_allowed(before, args.max_credits)
    print(f"TheirStack credits this month: {before.get('used_api_credits')}/{before.get('api_credits')} used; "
          f"reserve {RESERVE_CREDITS}; per-run cap {args.max_credits} -> this run may spend {allowed}"
          f"{' | since ' + str(since) if since else ' | backfill: last ' + str(LOOKBACK_DAYS) + ' days'}")
    if args.plan:
        return 0

    out_dir = RAW_DIR / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"source": "theirstack", "date": out_dir.name, "since": since, "max_credits": args.max_credits,
                "credits_allowed": allowed, "balance_before": before, "capped": False, "queries": [], "cursors": {}}

    spent, requests_made, errors = 0, 0, 0
    if allowed == 0:
        manifest["capped"] = True
        print("CAP REACHED: no TheirStack credits available for this run (monthly reserve or per-run cap). "
              "Skipping TheirStack today; this is not an error.")
    for query in args.queries:
        started = datetime.now(timezone.utc)
        discovered: list[datetime] = []
        entry = {"query": query, "status": "ok", "files": [], "n_jobs": 0,
                 "fetched_at": started.isoformat(timespec="seconds")}
        manifest["queries"].append(entry)
        for page in range(MAX_PAGES):
            limit = min(PAGE_SIZE, allowed - spent, QUERY_MAX_CREDITS.get(query, PAGE_SIZE * MAX_PAGES) - entry["n_jobs"])
            if limit <= 0:
                entry["status"] = "capped"
                manifest["capped"] = True
                if allowed:
                    print(f"[{query}] CAP REACHED after {spent} credits: newer postings left for later runs "
                          "(not an error).")
                break
            if requests_made:
                time.sleep(REQUEST_DELAY)
            requests_made += 1
            try:
                response = post_search(session, build_body(query, page, limit, since.get(query)))
                response.raise_for_status()
                jobs = response.json()["data"]
            except (requests.RequestException, ValueError, KeyError) as err:
                status = getattr(getattr(err, "response", None), "status_code", None)
                entry.update(status="error", error=f"{type(err).__name__} (HTTP {status})")
                errors += 1
                print(f"[{query}] page {page}: ERROR {type(err).__name__} (HTTP {status})")
                break
            text, _ = redact(response.text, ("THEIRSTACK_API_KEY",))
            out_file = out_dir / f"{query}_p{page}.json"
            out_file.write_text(text, encoding="utf-8")
            spent += len(jobs)
            discovered += discovered_times(jobs)
            entry["files"].append(out_file.name)
            entry["n_jobs"] += len(jobs)
            print(f"[{query}] page {page}: {len(jobs)} jobs -> {out_file.name}")
            if len(jobs) < limit:
                break
        else:
            entry["status"] = "page_limit"       # more may be waiting: continue from the cursor next run
            entry["note"] = f"free-plan limit of {MAX_PAGES} pages reached; the next run continues from here"
            print(f"[{query}] {entry['note']}")

        finished = entry["status"] == "ok"
        if not finished and discovered != sorted(discovered):
            # The cursor assumes oldest-first. If TheirStack ignores the (deprecated) order_by, keep it put.
            entry.update(status="error", error="results not oldest-first; cursor not moved")
            errors += 1
            print(f"[{query}] ERROR: {entry['error']}")
            discovered = []
        manifest["cursors"][query] = next_cursor(finished, discovered, started, since.get(query))

    try:
        after = get_balance(session)
        manifest["balance_after"] = after
        manifest["credits_used"] = int(after.get("used_api_credits") or 0) - int(before.get("used_api_credits") or 0)
    except (requests.RequestException, ValueError):
        manifest["credits_used"] = spent           # counted locally: 1 credit per job returned
    (out_dir / "_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(q["n_jobs"] for q in manifest["queries"])
    print(f"Done: {requests_made} requests, {total} postings, {manifest['credits_used']} credits used "
          f"-> data/raw/theirstack/{out_dir.name}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())

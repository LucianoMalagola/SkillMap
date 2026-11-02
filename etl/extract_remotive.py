"""
SkillMap: extract remote job postings from the Remotive public API.

Saves the full raw response per category to data/raw/remotive/<date>/<category>.json
plus a _manifest.json (status per category), so the transform step can be re-run
without new API calls.

Each category request returns that category's whole current list (no paging), so a
posting missing from a successful fetch has closed: the transform deactivates it.

Remotive terms (https://remotive.com/api-documentation, and the API's "0-legal-notice"):
  * max ~4 requests a day; more than ~2 a minute is blocked. Default = 2 categories =
    2 requests per run, 35 s apart.
  * Link back to the Remotive job URL and name Remotive as the source (jobs.url keeps the
    Remotive URL; the README credits Remotive).
  * Don't resubmit Remotive jobs to other job boards; don't use them to collect signups.
  * Jobs are delayed by 24 hours.

Usage:
    python etl/extract_remotive.py                       # data + operations
    python etl/extract_remotive.py --categories data
"""

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests

API_URL = "https://remotive.com/api/remote-jobs"
DEFAULT_CATEGORIES = ["data", "operations"]   # "Data and Analytics", "Operations"
REQUEST_DELAY = 35          # seconds; Remotive blocks more than ~2 requests a minute
MAX_REQUESTS_PER_RUN = 4    # their advised daily maximum

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "remotive"


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Remotive remote job postings")
    parser.add_argument("--categories", nargs="+", default=DEFAULT_CATEGORIES)
    args = parser.parse_args()
    if len(args.categories) > MAX_REQUESTS_PER_RUN:
        print(f"ERROR: Remotive advises max {MAX_REQUESTS_PER_RUN} requests a day; asked for {len(args.categories)}.")
        return 1

    out_dir = RAW_DIR / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "SkillMap job-market research (portfolio project)"

    entries, total = [], 0
    for i, category in enumerate(args.categories):
        if i:
            time.sleep(REQUEST_DELAY)
        entry = {"category": category, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            response = session.get(API_URL, params={"category": category}, timeout=60)
            response.raise_for_status()
            payload = response.json()
            jobs = payload["jobs"]
        except (requests.RequestException, ValueError, KeyError) as err:
            entries.append({**entry, "status": "error", "error": f"{type(err).__name__}: {err}"})
            print(f"[{category}] ERROR: {type(err).__name__}: {err}")
            continue
        out_file = out_dir / f"{category}.json"
        out_file.write_bytes(response.content)            # exactly as received
        entries.append({**entry, "status": "ok", "file": out_file.name, "n_jobs": len(jobs),
                        "job_count": payload.get("job-count")})
        total += len(jobs)
        print(f"[{category}] {len(jobs)} jobs -> {out_file.name}")

    (out_dir / "_manifest.json").write_text(json.dumps({"source": "remotive", "date": out_dir.name,
                                                        "categories": entries}, ensure_ascii=False, indent=2),
                                            encoding="utf-8")
    ok = [e for e in entries if e["status"] == "ok"]
    print(f"Done: {len(entries)} requests, {len(ok)} ok, {total} postings -> {out_dir.relative_to(ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

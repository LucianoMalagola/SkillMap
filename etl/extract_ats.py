"""
SkillMap: extract job postings from public ATS job boards (Greenhouse, Lever, Ashby).

Reads etl/companies.csv and, for each board, saves the full, unfiltered API
response exactly as received to:

    data/raw/<ats>/<date>/<board_token>.json

No filtering or parsing happens here (see CLAUDE.md, "Location scope and
filtering"): the transform step reads these files, so it can be re-run without
spending API calls.

Each <ats>/<date>/ folder also gets a _manifest.json listing every board with
its status, job count and file. The load step must use it: a board whose fetch
failed has no file today, and its jobs must NOT be marked inactive because of that.

Usage:
    python etl/extract_ats.py                          # every board in companies.csv
    python etl/extract_ats.py --ats lever ashby
    python etl/extract_ats.py --companies Tebra "Kepler Group"
"""

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_COMPANIES = ROOT / "etl" / "companies.csv"
RAW_DIR = ROOT / "data" / "raw"

# Per-ATS endpoint (the variant that includes full descriptions) and where the
# job list lives in the response.
ATS = {
    "greenhouse": {
        "url": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        "jobs": lambda payload: payload["jobs"],
    },
    "lever": {
        "url": "https://api.lever.co/v0/postings/{token}?mode=json",
        "jobs": lambda payload: payload,          # the response is the list itself
    },
    "ashby": {
        "url": "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true",
        "jobs": lambda payload: payload["jobs"],
    },
}

REQUEST_DELAY = 1.0      # seconds between boards (be polite)
MAX_ATTEMPTS = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}
TIMEOUT = 90             # some boards return 5+ MB

session = requests.Session()
session.headers["User-Agent"] = "SkillMap job-market research (portfolio project)"


def safe_filename(token: str) -> str:
    """'scale army careers' -> 'scale_army_careers'; keeps dots ('freebalance.com')."""
    return re.sub(r"[^a-z0-9._-]+", "_", token.lower()).strip("_")


def fetch(url: str) -> requests.Response:
    """GET with retries on network errors, rate limits and server errors."""
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = session.get(url, timeout=TIMEOUT)
            if response.status_code not in RETRY_STATUSES:
                return response
            last_error = f"HTTP {response.status_code}"
            wait = int(response.headers.get("Retry-After", 0) or 0) or 5 * attempt
        except requests.RequestException as err:
            last_error = type(err).__name__
            wait = 5 * attempt
        if attempt < MAX_ATTEMPTS:
            time.sleep(wait)
    raise RuntimeError(f"gave up after {MAX_ATTEMPTS} attempts ({last_error})")


def extract_board(company: dict, out_dir: Path) -> dict:
    """Fetch one board, save the raw body, return its manifest entry."""
    ats, token = company["ats"], company["board_token"]
    url = ATS[ats]["url"].format(token=quote(token))
    entry = {"company": company["company"], "board_token": token, "market": company["market"],
             "url": url, "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        response = fetch(url)
        if response.status_code != 200:
            return {**entry, "status": "error", "error": f"HTTP {response.status_code}"}
        n_jobs = len(ATS[ats]["jobs"](response.json()))   # also checks the body is valid JSON
    except (RuntimeError, ValueError, KeyError, TypeError) as err:
        return {**entry, "status": "error", "error": str(err) or type(err).__name__}

    out_file = out_dir / f"{safe_filename(token)}.json"
    out_file.write_bytes(response.content)                # exactly as received
    return {**entry, "status": "ok", "n_jobs": n_jobs, "file": out_file.name,
            "bytes": len(response.content)}


def update_manifest(out_dir: Path, entries: list[dict]) -> None:
    """Merge this run's entries into the day's manifest (keyed by board token), so a
    partial re-run (--companies) doesn't erase the other boards' records."""
    path = out_dir / "_manifest.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"boards": []}
    boards = {b["board_token"]: b for b in existing["boards"]}
    boards.update({e["board_token"]: e for e in entries})
    manifest = {"source": out_dir.parent.name, "date": out_dir.name,
                "boards": sorted(boards.values(), key=lambda b: b["company"])}
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract postings from Greenhouse/Lever/Ashby boards")
    parser.add_argument("--companies-file", type=Path, default=DEFAULT_COMPANIES)
    parser.add_argument("--ats", nargs="+", choices=sorted(ATS), default=sorted(ATS))
    parser.add_argument("--companies", nargs="+", help="only these companies (name or board token)")
    args = parser.parse_args()

    with args.companies_file.open(newline="", encoding="utf-8") as f:
        companies = [c for c in csv.DictReader(f) if c["ats"] in args.ats]
    if args.companies:
        wanted = {w.lower() for w in args.companies}
        companies = [c for c in companies if c["company"].lower() in wanted or c["board_token"].lower() in wanted]
    if not companies:
        print("No boards selected.")
        return 1

    today = date.today().isoformat()
    results: dict[str, list[dict]] = {}
    print(f"Extracting {len(companies)} boards...\n")
    for i, company in enumerate(sorted(companies, key=lambda c: (c["ats"], c["company"]))):
        if i:
            time.sleep(REQUEST_DELAY)
        out_dir = RAW_DIR / company["ats"] / today
        out_dir.mkdir(parents=True, exist_ok=True)
        entry = extract_board(company, out_dir)
        results.setdefault(company["ats"], []).append(entry)
        detail = f"{entry['n_jobs']:>4} jobs  {entry['bytes'] / 1e6:5.2f} MB" if entry["status"] == "ok" \
            else f"ERROR: {entry['error']}"
        print(f"  {company['ats']:<10} {company['company']:<26} {detail}")

    for ats, entries in results.items():
        update_manifest(RAW_DIR / ats / today, entries)

    all_entries = [e for entries in results.values() for e in entries]
    ok = [e for e in all_entries if e["status"] == "ok"]
    failed = [e for e in all_entries if e["status"] != "ok"]
    print(f"\nDone: {len(ok)}/{len(all_entries)} boards, {sum(e['n_jobs'] for e in ok)} postings, "
          f"{sum(e['bytes'] for e in ok) / 1e6:.1f} MB -> data/raw/<ats>/{today}/")
    if failed:
        print("FAILED (recorded in _manifest.json; the load step must not deactivate their jobs): "
              + ", ".join(f"{e['company']} ({e['error']})" for e in failed))
    # Partial failures are expected now and then (a board goes down, a company
    # leaves an ATS). Only fail the run when nothing at all could be fetched.
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

"""
SkillMap: extract job postings from the Jooble REST API (Costa Rica).

Saves the raw API response for each search to data/raw/jooble/<date>/ so the
transform step can be re-run without spending API calls.

Usage:
    python etl/extract_jooble.py                 # all roles, 1 page each
    python etl/extract_jooble.py --pages 2
    python etl/extract_jooble.py --roles "data analyst"
"""

import argparse
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parent))
from redact import redact  # noqa: E402

# Main Jooble domain. The country is set through the "location" field.
# (Country subdomains such as cr.jooble.org returned 403 for this key.)
DEFAULT_HOST = "https://jooble.org/api/"

DEFAULT_ROLES = [
    "data analyst",
    "business intelligence analyst",
    "business operations analyst",
    "data reporting",
]

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "jooble"


def fetch_page(host: str, api_key: str, keywords: str, location: str, page: int) -> dict:
    """POST one search request to Jooble and return the parsed JSON."""
    response = requests.post(
        f"{host}{api_key}",
        json={"keywords": keywords, "location": location, "page": str(page)},
        headers={"Content-Type": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Jooble job postings")
    parser.add_argument("--roles", nargs="+", default=DEFAULT_ROLES)
    parser.add_argument("--location", default="Costa Rica")
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--host", default=DEFAULT_HOST)
    args = parser.parse_args()

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():          # locally; in GitHub Actions the key comes from the environment
        load_dotenv(env_path)
    api_key = os.getenv("JOOBLE_API_KEY", "").strip()
    if not api_key:
        print("ERROR: JOOBLE_API_KEY is not set (environment or .env).")
        print("In .env add a line like: JOOBLE_API_KEY=your-key  (no quotes, no spaces around '=').")
        return 1

    out_dir = RAW_DIR / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    total_jobs, ok_requests = 0, 0
    for role in args.roles:
        for page in range(1, args.pages + 1):
            try:
                data = fetch_page(args.host, api_key, role, args.location, page)
            except requests.HTTPError as err:
                # never print err itself: its message contains the request URL, i.e. the key
                print(f"[{role} p{page}] HTTP error {err.response.status_code if err.response is not None else '?'}")
                continue
            except requests.RequestException as err:
                print(f"[{role} p{page}] network error: {type(err).__name__}")
                continue
            ok_requests += 1

            jobs = data.get("jobs", [])
            total_jobs += len(jobs)
            out_file = out_dir / f"{role.replace(' ', '_')}_p{page}.json"
            clean, _ = redact(json.dumps(data, ensure_ascii=False, indent=2))   # never save a key
            out_file.write_text(clean, encoding="utf-8")
            print(f"[{role} p{page}] {len(jobs)} jobs (totalCount={data.get('totalCount')}) -> {out_file.name}")

            if not jobs:
                break          # no more results for this role
            time.sleep(1)      # be polite to the API

    print(f"Done: {ok_requests} requests ok, {total_jobs} postings saved to {out_dir}")
    return 0 if ok_requests else 1


if __name__ == "__main__":
    sys.exit(main())

"""
SkillMap: extract US remote job postings from the Adzuna API.

Saves each raw API response to data/raw/adzuna/<date>/<role>_p<page>.json plus a
_manifest.json describing the queries (without credentials), so the transform step can
be re-run without spending API calls.

Targeting: Adzuna has no remote filter. Each query puts the role in the TITLE
(title_only) and requires "remote" somewhere in the ad (what=remote); the transform
step still classifies every posting's location (etl/location.py).

Budget: the free tier is about 1,000 calls/month. Default = 4 roles x 1 page = 4 calls
per run (~120/month if run daily). --max-calls caps a single run.

Adzuna descriptions are snippets (~500 chars): rows are loaded with
description_is_full = FALSE (market volume, not tool demand).
Terms: personal/academic research use; credit "The Adzuna API" with a link
(https://www.adzuna.com).

Usage:
    python etl/extract_adzuna.py                          # all roles, 1 page each
    python etl/extract_adzuna.py --roles "data analyst" --pages 1
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

DEFAULT_ROLES = [
    "data analyst",
    "business intelligence analyst",
    "business operations analyst",
    "reporting analyst",
]

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "adzuna"
REQUEST_DELAY = 2.0


def fetch_page(app_id: str, app_key: str, country: str, role: str, page: int, per_page: int, max_days: int) -> tuple[int, dict | None]:
    """One search request. Returns (status_code, payload). Never raises with the URL
    (it contains the credentials)."""
    params = {
        "app_id": app_id, "app_key": app_key,
        "title_only": role, "what": "remote",
        "results_per_page": per_page, "max_days_old": max_days, "sort_by": "date",
        "content-type": "application/json",
    }
    try:
        response = requests.get(API_URL.format(country=country, page=page), params=params, timeout=30)
    except requests.RequestException as err:
        return -1, {"error": type(err).__name__}
    if response.status_code != 200:
        return response.status_code, None
    try:
        return 200, response.json()
    except ValueError:
        return -2, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract Adzuna US remote job postings")
    parser.add_argument("--roles", nargs="+", default=DEFAULT_ROLES)
    parser.add_argument("--pages", type=int, default=1)
    parser.add_argument("--per-page", type=int, default=50)
    parser.add_argument("--max-days", type=int, default=7, help="only ads posted in the last N days")
    parser.add_argument("--country", default="us")
    parser.add_argument("--max-calls", type=int, default=10, help="hard cap on API calls for this run")
    args = parser.parse_args()

    env_path = ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    app_id = os.getenv("ADZUNA_APP_ID", "").strip()
    app_key = os.getenv("ADZUNA_APP_KEY", "").strip()
    if not app_id or not app_key:
        print("ERROR: ADZUNA_APP_ID and ADZUNA_APP_KEY must be set (environment or .env).")
        return 1

    out_dir = RAW_DIR / date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    calls, total_jobs, queries = 0, 0, []
    for role in args.roles:
        for page in range(1, args.pages + 1):
            if calls >= args.max_calls:
                print(f"Stopping: reached --max-calls={args.max_calls}")
                break
            if calls:
                time.sleep(REQUEST_DELAY)
            status, data = fetch_page(app_id, app_key, args.country, role, page, args.per_page, args.max_days)
            calls += 1
            query = {"role": role, "page": page, "title_only": role, "what": "remote", "country": args.country,
                     "max_days_old": args.max_days, "results_per_page": args.per_page,
                     "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "status": status}
            if status != 200 or data is None:
                print(f"[{role} p{page}] failed (status {status})")
                queries.append({**query, "ok": False})
                break
            jobs = data.get("results", [])
            total_jobs += len(jobs)
            out_file = out_dir / f"{role.replace(' ', '_')}_p{page}.json"
            out_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            queries.append({**query, "ok": True, "file": out_file.name, "n_jobs": len(jobs), "count": data.get("count")})
            print(f"[{role} p{page}] {len(jobs)} jobs (count={data.get('count')}) -> {out_file.name}")
            if len(jobs) < args.per_page:
                break          # last page for this role

    (out_dir / "_manifest.json").write_text(json.dumps({"source": "adzuna", "date": out_dir.name, "queries": queries},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Done: {calls} API calls, {total_jobs} postings -> {out_dir.relative_to(ROOT)}  "
          f"(~{calls * 30} calls/month if run daily; free tier ~1,000)")
    return 0 if any(q["ok"] for q in queries) else 1


if __name__ == "__main__":
    sys.exit(main())

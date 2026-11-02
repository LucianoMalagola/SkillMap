"""
SkillMap: discover which candidate companies have a live public job board on
Greenhouse, Lever or Ashby.

For every company in etl/company_candidates.csv, each slug is tried on all three
ATSs (so a wrong guess about which ATS a company uses costs nothing). A board is
confirmed when the endpoint answers 200 AND has at least one open posting. If a
company is found on more than one ATS (usually an abandoned old board), the one
with the most postings wins.

Outputs:
  etl/companies.csv                          confirmed boards (committed)
  data/raw/boards/<date>/discovery.json      full probe results (not committed)

Usage:
    python etl/discover_boards.py
    python etl/discover_boards.py --candidates etl/company_candidates.csv --workers 8
"""

import argparse
import csv
import json
import re
import sys
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATES = ROOT / "etl" / "company_candidates.csv"
DEFAULT_OUTPUT = ROOT / "etl" / "companies.csv"
RAW_DIR = ROOT / "data" / "raw" / "boards"

# Public, unauthenticated list endpoints for each ATS.
ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
}
GREENHOUSE_BOARD_INFO = "https://boards-api.greenhouse.io/v1/boards/{token}"

# Titles that look like the roles SkillMap tracks (used only for reporting).
DATA_ROLE_RE = re.compile(
    r"(data|\bbi\b|business intelligence|analytics|reporting|insights|marketing science"
    r"|business operations|bizops|revenue operations|sales operations|business analyst)"
    r".{0,40}analyst|\bbusiness analyst|analyst.{0,40}(data|analytics|reporting|\bbi\b|insights)",
    re.IGNORECASE,
)
COSTA_RICA_RE = re.compile(r"costa\s*rica|san jos[eé]\b|heredia|alajuela|cartago|escaz[uú]", re.IGNORECASE)

session = requests.Session()
session.headers["User-Agent"] = "SkillMap board discovery (portfolio project)"


def normalize_jobs(ats: str, payload) -> list[dict]:
    """Reduce each ATS's job objects to {title, location} for reporting."""
    if ats == "greenhouse":
        return [
            {"title": j.get("title", ""), "location": (j.get("location") or {}).get("name", "")}
            for j in payload.get("jobs", [])
        ]
    if ats == "lever":
        jobs = []
        for j in payload if isinstance(payload, list) else []:
            cats = j.get("categories") or {}
            locs = cats.get("allLocations") or [cats.get("location", "")]
            jobs.append({"title": j.get("text", ""), "location": " | ".join(filter(None, locs))})
        return jobs
    if ats == "ashby":
        jobs = []
        for j in payload.get("jobs", []):
            locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
            country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
            if country:
                locs.append(country)
            if j.get("isRemote"):
                locs.append("Remote")
            jobs.append({"title": j.get("title", ""), "location": " | ".join(filter(None, locs))})
        return jobs
    raise ValueError(ats)


def probe(ats: str, token: str) -> dict:
    """Hit one ATS endpoint for one token and summarise the answer."""
    url = ENDPOINTS[ats].format(token=quote(token))
    result = {"ats": ats, "token": token, "url": url, "status": None, "n_jobs": 0}
    try:
        response = session.get(url, timeout=20)
    except requests.RequestException as err:
        result["status"] = f"error: {type(err).__name__}"
        return result

    result["status"] = response.status_code
    if response.status_code != 200:
        return result
    try:
        jobs = normalize_jobs(ats, response.json())
    except (ValueError, AttributeError):
        result["status"] = "bad json"
        return result

    result["n_jobs"] = len(jobs)
    result["n_cr_jobs"] = sum(bool(COSTA_RICA_RE.search(j["location"])) for j in jobs)
    result["n_data_roles"] = sum(bool(DATA_ROLE_RE.search(j["title"])) for j in jobs)
    result["data_role_titles"] = [j["title"] for j in jobs if DATA_ROLE_RE.search(j["title"])][:5]
    result["sample"] = jobs[:3]

    # Greenhouse exposes the board's display name: lets us catch slug collisions
    # (e.g. a generic slug that belongs to a different company).
    if ats == "greenhouse" and jobs:
        try:
            info = session.get(GREENHOUSE_BOARD_INFO.format(token=quote(token)), timeout=20).json()
            result["board_name"] = info.get("name")
        except (requests.RequestException, ValueError):
            pass
    return result


def load_candidates(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["slugs"] = [s.strip().lower() for s in row["slugs"].split(";") if s.strip()]
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover live Greenhouse/Lever/Ashby boards")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    candidates = load_candidates(args.candidates)
    tasks = [(c["company"], ats, slug) for c in candidates for slug in c["slugs"] for ats in ENDPOINTS]
    print(f"Probing {len(tasks)} endpoints for {len(candidates)} candidates...\n")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(lambda t: {"company": t[0], **probe(t[1], t[2])}, tasks))

    confirmed, report = [], []
    for cand in candidates:
        hits = [r for r in results if r["company"] == cand["company"] and r["n_jobs"] > 0]
        empty = [r for r in results if r["company"] == cand["company"] and r["status"] == 200 and r["n_jobs"] == 0]
        best = max(hits, key=lambda r: r["n_jobs"]) if hits else None
        report.append({"company": cand["company"], "market": cand["market"], "best": best,
                       "other_hits": [h for h in hits if h is not best], "empty_boards": empty})
        if best:
            confirmed.append({"company": cand["company"], "ats": best["ats"],
                              "board_token": best["token"], "market": cand["market"]})

    # ---- console summary ----
    header = f"{'company':<28} {'mkt':<9} {'ats':<10} {'token':<24} {'jobs':>5} {'CR':>4} {'data':>5}  board name"
    print(header)
    print("-" * len(header))
    for r in sorted(report, key=lambda r: (r["best"] is None, r["market"], r["company"])):
        b = r["best"]
        if b:
            extra = f"  {b.get('board_name') or ''}"
            if r["other_hits"]:
                extra += "  (also: " + ", ".join(f"{h['ats']}/{h['token']}={h['n_jobs']}" for h in r["other_hits"]) + ")"
            print(f"{r['company']:<28} {r['market']:<9} {b['ats']:<10} {b['token']:<24} "
                  f"{b['n_jobs']:>5} {b['n_cr_jobs']:>4} {b['n_data_roles']:>5}{extra}")
        else:
            note = "empty board on " + ", ".join(f"{e['ats']}/{e['token']}" for e in r["empty_boards"]) \
                if r["empty_boards"] else "not found"
            print(f"{r['company']:<28} {r['market']:<9} {'-':<10} {'-':<24} {'':>5} {'':>4} {'':>5}  {note}")

    n_cr = sum(c["market"] == "CR" for c in confirmed)
    print(f"\nConfirmed {len(confirmed)}/{len(candidates)} "
          f"({n_cr} CR, {len(confirmed) - n_cr} US-remote)")

    # ---- outputs ----
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "ats", "board_token", "market"])
        writer.writeheader()
        writer.writerows(sorted(confirmed, key=lambda c: (c["market"], c["company"])))
    print(f"Saved {args.output.relative_to(ROOT)}")

    raw_dir = RAW_DIR / date.today().isoformat()
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Full probe results: {(raw_dir / 'discovery.json').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

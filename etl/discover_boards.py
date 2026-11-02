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
    python etl/discover_boards.py --candidates etl/company_candidates.csv --workers 4
"""

import argparse
import csv
import json
import re
import sys
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize import parse_board

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATES = ROOT / "etl" / "company_candidates.csv"
DEFAULT_OUTPUT = ROOT / "etl" / "companies.csv"
RAW_DIR = ROOT / "data" / "raw" / "boards"

# Public, unauthenticated list endpoints for each ATS.
ENDPOINTS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
    "lever": "https://api.lever.co/v0/postings/{token}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{token}",
    "recruitee": "https://{token}.recruitee.com/api/offers/",
    "personio": "https://{token}.jobs.personio.de/xml",
}
GREENHOUSE_BOARD_INFO = "https://boards-api.greenhouse.io/v1/boards/{token}"
REQUEST_DELAY = 0.5  # seconds each worker waits after a request
# At most one request per second to any single platform domain, whatever the number of workers
# (api.lever.co's robots.txt sets Crawl-delay: 1).
MIN_INTERVAL_PER_HOST = 1.0
MAX_ATTEMPTS = 3
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Titles that look like the roles SkillMap tracks (used only for reporting).
DATA_ROLE_RE = re.compile(
    r"(data|\bbi\b|business intelligence|analytics|reporting|insights|marketing science"
    r"|business operations|bizops|revenue operations|sales operations|business analyst)"
    r".{0,40}analyst|\bbusiness analyst|analyst.{0,40}(data|analytics|reporting|\bbi\b|insights)",
    re.IGNORECASE,
)
COSTA_RICA_RE = re.compile(r"costa\s*rica|san jos[eé]\b|heredia|alajuela|cartago|escaz[uú]", re.IGNORECASE)

_host_locks: dict[str, threading.Lock] = {}
_host_last: dict[str, float] = {}
_locks_guard = threading.Lock()


def _wait_for_host(url: str) -> None:
    """Block until MIN_INTERVAL_PER_HOST has passed since the last request to this platform.
    Keyed by registrable domain, not hostname: {company}.jobs.personio.de and
    {company}.recruitee.com are many hostnames on one platform (Personio answered 429
    when each subdomain was rate-limited separately)."""
    host = ".".join((urlsplit(url).hostname or "").split(".")[-2:])
    with _locks_guard:
        lock = _host_locks.setdefault(host, threading.Lock())
    with lock:
        wait = _host_last.get(host, 0) + MIN_INTERVAL_PER_HOST - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _host_last[host] = time.monotonic()


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
    if ats == "recruitee":
        return [{"title": j.get("title", ""),
                 "location": " | ".join(filter(None, [loc.get("city") or loc.get("name") for loc in j.get("locations") or []]
                                               + [loc.get("country") for loc in j.get("locations") or []]
                                               + (["Remote"] if j.get("remote") else [])))}
                for j in payload.get("offers", [])]
    if ats == "personio":
        return [{"title": j.get("name", ""),
                 "location": " | ".join(filter(None, [j.get("office")] + j.get("additionalOffices", [])))}
                for j in payload]
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
    # Retry network errors, rate limits and server errors so a transient failure
    # isn't mistaken for "this company has no board".
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            _wait_for_host(url)
            response = session.get(url, timeout=60)  # some boards return several MB
            error = f"HTTP {response.status_code}" if response.status_code in RETRY_STATUSES else None
        except requests.RequestException as err:
            response, error = None, type(err).__name__
        time.sleep(REQUEST_DELAY * attempt)  # be polite: pause between requests, longer after failures
        if error is None:
            break
    if error:
        result["status"] = f"error: {error}"
        return result

    result["status"] = response.status_code
    if response.status_code != 200:
        return result
    try:
        payload = parse_board("personio", response.content) if ats == "personio" else response.json()
        jobs = normalize_jobs(ats, payload)
    except (ValueError, AttributeError, ET.ParseError):
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
            _wait_for_host(GREENHOUSE_BOARD_INFO)
            info = session.get(GREENHOUSE_BOARD_INFO.format(token=quote(token)), timeout=20).json()
            result["board_name"] = info.get("name")
        except (requests.RequestException, ValueError):
            pass
    return result


def display(path: Path) -> str:
    """Show paths inside the repo relative to it, others in full."""
    path = path.resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def load_candidates(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        row["slugs"] = [s.strip().lower() for s in row["slugs"].split(";") if s.strip()]
    # market = "excluded" keeps a researched company on record (e.g. its slug matches a
    # different company's board, or its board has no CR/US-remote postings) without
    # probing it again.
    return [row for row in rows if row["market"] != "excluded"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover live Greenhouse/Lever/Ashby boards")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--allow-errors", action="store_true",
                        help="save companies.csv even if some requests kept failing")
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
        errors = [r for r in results if r["company"] == cand["company"] and str(r["status"]).startswith("error")]
        best = max(hits, key=lambda r: r["n_jobs"]) if hits else None
        report.append({"company": cand["company"], "market": cand["market"], "best": best,
                       "other_hits": [h for h in hits if h is not best], "empty_boards": empty,
                       "errors": errors})
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
            if r["errors"]:
                note = "ERROR on " + ", ".join(f"{e['ats']}/{e['token']} ({e['status']})" for e in r["errors"])
            elif r["empty_boards"]:
                note = "empty board on " + ", ".join(f"{e['ats']}/{e['token']}" for e in r["empty_boards"])
            else:
                note = "not found"
            print(f"{r['company']:<28} {r['market']:<9} {'-':<10} {'-':<24} {'':>5} {'':>4} {'':>5}  {note}")

    n_cr = sum(c["market"] == "CR" for c in confirmed)
    print(f"\nConfirmed {len(confirmed)}/{len(candidates)} "
          f"({n_cr} CR, {len(confirmed) - n_cr} US-remote)")

    # ---- outputs ----
    raw_dir = RAW_DIR / date.today().isoformat()
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "discovery.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Full probe results: {display(raw_dir / 'discovery.json')}")

    # A company whose only answers were errors might still have a live board, so
    # don't overwrite the confirmed list with a possibly shorter one.
    unresolved = [r["company"] for r in report if r["best"] is None and r["errors"]]
    if unresolved and not args.allow_errors:
        print(f"\nNOT saving {display(args.output)}: requests kept failing for "
              f"{', '.join(unresolved)}. Re-run, or pass --allow-errors to save anyway.")
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["company", "ats", "board_token", "market"])
        writer.writeheader()
        writer.writerows(sorted(confirmed, key=lambda c: (c["market"], c["company"])))
    print(f"Saved {display(args.output)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

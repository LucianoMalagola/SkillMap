"""TheirStack source: normalization, description fullness, cross-source dedup, listing merge,
credit caps, pipeline wiring and the incremental-source exemptions in the quality checks.
Synthetic postings shaped like the real API responses of 2026-11-02 (no personal data)."""

from __future__ import annotations

import json
import sys
from collections import Counter
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "etl"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import extract_theirstack as ts
import quality_checks
import run_pipeline
import transform_load
from location import classify_posting
from normalize import ats_refs, looks_full, match_key, normalize, strip_markdown
from skills import SkillExtractor
from transform_load import api_units_used, drop_ats_copies, load, read_raw, transform

BODY = ("### **About the role**\n\nWe are looking for a **Data Analyst** to build dashboards in Power BI "
        "and write SQL queries against our warehouse. " + "You will partner with finance and operations teams "
        "to define metrics, automate reports and present findings to leadership. " * 12)


def job(**over) -> dict:
    base = {
        "id": 900000001, "job_title": "Data Analyst", "company": "Acme", "company_domain": "acme.com",
        "description": BODY, "final_url": "https://acme.wd5.myworkdayjobs.com/Careers/job/Heredia/Data-Analyst_R123456",
        "source_url": "https://www.linkedin.com/jobs/view/1", "url": "https://acme.wd5.myworkdayjobs.com/x",
        "date_posted": "2026-10-20", "discovered_at": "2026-10-20T10:00:00Z", "closed_at": None,
        "remote": False, "hybrid": False, "country": "Costa Rica", "country_code": "CR",
        "location": "Heredia", "long_location": "Heredia",
        "locations": [{"display_name": "Heredia, Heredia, Costa Rica", "country_code": "CR"}],
        "min_annual_salary": None, "max_annual_salary": None, "salary_currency": None,
    }
    return {**base, **over}


# ---- normalize -----------------------------------------------------------------------------

def test_normalize_maps_fields_and_strips_markdown():
    row = normalize("theirstack", job(), "")
    assert row["external_id"] == "900000001" and row["company"] == "Acme" and row["title"] == "Data Analyst"
    assert row["url"].startswith("https://acme.wd5.myworkdayjobs.com/Careers/")       # final_url, not LinkedIn
    assert row["location_raw"] == "Heredia, Heredia, Costa Rica"
    assert "###" not in row["description"] and "**" not in row["description"]
    assert row["description"].startswith("About the role")
    assert row["posted_at"].date().isoformat() == "2026-10-20"
    assert row["workplace_type"] is None


def test_workplace_and_remote_location():
    assert normalize("theirstack", job(hybrid=True), "")["workplace_type"] == "hybrid"
    row = normalize("theirstack", job(remote=True), "")
    assert row["workplace_type"] == "remote" and row["location_raw"].startswith("Remote - ")


def test_salary_uses_theirstack_annual_fields():
    row = normalize("theirstack", job(min_annual_salary=24000, max_annual_salary=36000, salary_currency="USD"), "")
    assert (row["salary_min"], row["salary_max"], row["salary_currency"]) == (24000, 36000, "USD")
    assert normalize("theirstack", job(), "")["salary_min"] is None


def test_description_is_full_is_measured_not_assumed():
    assert normalize("theirstack", job(), "")["description_is_full"] is True
    short = "Data Analyst needed for reporting with Excel and SQL. Apply now..."
    assert normalize("theirstack", job(description=short), "")["description_is_full"] is False
    # other sources keep their known values
    assert normalize("adzuna", {"id": 1, "title": "Data Analyst", "description": BODY}, "")["description_is_full"] is False


def test_looks_full():
    assert looks_full(BODY)
    assert not looks_full("Short snippet about a data analyst role with SQL.")
    assert not looks_full(BODY + " and more…")          # cut off by the source


def test_strip_markdown():
    assert strip_markdown("## Title\n\n**Bold** and *italic* and [link](https://x.y)\n* item") == \
        "Title\n\nBold and italic and link\n- item"
    assert strip_markdown("Power BI \\- SQL") == "Power BI - SQL"


# ---- location ------------------------------------------------------------------------------

def test_location_classification():
    assert classify_posting("theirstack", job())[0] == "CR"
    latam = job(remote=True, country="Brazil", country_code="BR",
                locations=[{"display_name": "Remote, Latin America", "country_code": None}])
    assert classify_posting("theirstack", latam)[0] == "LATAM-remote"
    onsite_mx = job(country="Mexico", country_code="MX",
                    locations=[{"display_name": "Monterrey, Nuevo Leon, Mexico", "country_code": "MX"}])
    assert classify_posting("theirstack", onsite_mx)[0] is None


# ---- cross-source dedup ----------------------------------------------------------------------

def test_ats_refs_from_links():
    assert ats_refs("https://www.vml.com/careers/job/8924729002?gh_jid=8924729002") == {("greenhouse", "8924729002")}
    assert ats_refs("https://job-boards.greenhouse.io/wundermanthompson/jobs/8846271002") == \
        {("greenhouse", "8846271002")}
    uuid = "0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0"
    assert ats_refs(f"https://jobs.lever.co/acme/{uuid}/apply") == {("lever", uuid)}
    assert ats_refs(f"https://jobs.ashbyhq.com/acme/{uuid}") == {("ashby", uuid)}
    assert ats_refs("https://acme.jobs.personio.de/job/123456?display=en") == {("personio", "123456")}
    assert ats_refs("https://www.linkedin.com/jobs/view/123", None) == set()


def test_match_key_normalises_company_and_title():
    assert match_key("VML Inc.", "Senior Data Analyst (Hybrid)") == ("vml", "senior data analyst")
    assert match_key("Análisis S.A.", "Analista de Datos") == match_key("analisis", "ANALISTA DE DATOS")


def test_drop_ats_copies_keeps_ats_version():
    rows = [{"company": "VML", "title": "Senior Data Analyst", "ats_refs": {("greenhouse", "1")}},
            {"company": "Acme Corp", "title": "Data Analyst", "ats_refs": set()},
            {"company": "Other", "title": "BI Analyst", "ats_refs": set()}]
    excluded = Counter()
    kept = drop_ats_copies("theirstack", rows, excluded, ids={("greenhouse", "1")},
                           keys={match_key("Acme", "Data Analyst")})
    assert [r["company"] for r in kept] == ["Other"] and excluded["duplicate_ats"] == 2
    # ATS sources themselves are never dropped
    assert drop_ats_copies("greenhouse", rows, Counter(), {("greenhouse", "1")}, set()) == rows


# ---- one posting listed on several sites ---------------------------------------------------------

def test_listing_copies_are_merged_by_requisition_or_text():
    copies = [
        ("", job(id=1, company="Hpe", company_domain=None,
                 final_url="https://hpe.wd5.myworkdayjobs.com/A/job/Heredia/Business-Operations-Analyst_1209477-1",
                 job_title="Business Operations Analyst", closed_at=None, discovered_at="2026-10-16T02:00:00Z")),
        ("", job(id=2, company="Hpe Wfmathpe", company_domain=None,
                 final_url="https://hpe.wd5.myworkdayjobs.com/B/job/Heredia/Business-Operations-Analyst_1209477-2",
                 job_title="Business Operations Analyst", discovered_at="2026-10-16T01:00:00Z")),
        ("", job(id=3, company="Hewlett Packard Enterprise (HPE)", company_domain="hpe.com",
                 description=BODY + " Slightly different footer.",
                 final_url="https://careers.hpe.com/us/en/job/HPE1US1209477EXTERNALENUS/Business-Operations-Analyst",
                 job_title="Business Operations Analyst", closed_at="2026-10-20T00:00:00Z")),
        ("", job(id=4, job_title="Data Analyst", final_url="https://acme.example/jobs/777777")),
    ]
    rows, excluded = transform("theirstack", copies, SkillExtractor({"sql": "SQL"}))
    assert len(rows) == 2 and excluded["duplicate"] == 2
    hpe = next(r for r in rows if r["title"] == "Business Operations Analyst")
    assert hpe["company"] == "Hewlett Packard Enterprise (HPE)"     # the copy with a company domain
    assert hpe["closed_at"] is None                                  # open while any copy is open
    assert hpe["discovered_at"] == "2026-10-16T01:00:00Z"            # first discovery


# ---- extractor: caps and request body ---------------------------------------------------------------

def test_credits_allowed_respects_month_and_run_caps():
    assert ts.credits_allowed({"api_credits": 200, "used_api_credits": 28}, 150) == 150
    assert ts.credits_allowed({"api_credits": 200, "used_api_credits": 100}, 150) == 90     # 200-100-10 reserve
    assert ts.credits_allowed({"api_credits": 200, "used_api_credits": 195}, 150) == 0
    assert ts.credits_allowed({"api_credits": 200, "used_api_credits": 0}, 15) == 15


def test_build_body_requires_final_url_and_respects_free_plan():
    body = ts.build_body("cr", 0, 25, None)
    assert body["property_exists_or"] == ["final_url"] and body["job_country_code_or"] == ["CR"]
    assert body["posted_at_max_age_days"] == 90 and "discovered_at_gte" not in body
    assert ts.build_body("latam_remote", 1, 5, "2026-11-02T10:00:00+00:00")["discovered_at_gte"].startswith("2026-11-02")
    assert ts.PAGE_SIZE <= 25 and ts.MAX_PAGES <= 5


def test_cap_reached_spends_nothing_and_does_not_fail(monkeypatch, tmp_path, capsys):
    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    class FakeSession:
        headers: dict = {}

        def get(self, *a, **k):
            return FakeResponse({"api_credits": 200, "used_api_credits": 195})

        def post(self, *a, **k):
            raise AssertionError("no search request may be made once the cap is reached")

    monkeypatch.setenv("THEIRSTACK_API_KEY", "x" * 20)
    monkeypatch.setattr(ts.requests, "Session", FakeSession)
    monkeypatch.setattr(ts, "RAW_DIR", tmp_path)
    assert ts.main([]) == 0
    assert "CAP REACHED" in capsys.readouterr().out
    assert read_raw("theirstack", next(tmp_path.iterdir())) == ([], None, 0, False)   # transform sees a clean empty day


def test_zero_new_postings_is_a_normal_run(monkeypatch, tmp_path, capsys):
    """Real extractor -> manifest -> read_raw -> transform on an empty search result."""
    class FakeResponse:
        status_code = 200
        text = '{"metadata": {"total_results": 0}, "data": []}'

        def raise_for_status(self):
            pass

        def json(self):
            return {"data": []}

    class Balance(FakeResponse):
        def json(self):
            return {"api_credits": 200, "used_api_credits": 169}

    class FakeSession:
        headers: dict = {}

        def get(self, *a, **k):
            return Balance()

        def post(self, *a, **k):
            return FakeResponse()

    monkeypatch.setenv("THEIRSTACK_API_KEY", "x" * 20)
    monkeypatch.setattr(ts.requests, "Session", FakeSession)
    monkeypatch.setattr(ts, "RAW_DIR", tmp_path)
    monkeypatch.setattr(ts, "REQUEST_DELAY", 0)
    assert ts.main(["--since", "2026-11-03T22:00:00+00:00"]) == 0

    day_dir = next(tmp_path.iterdir())
    items, ok_companies, failed, full_inventory = read_raw("theirstack", day_dir)
    assert (items, ok_companies, failed, full_inventory) == ([], None, 0, False)
    assert transform("theirstack", items, SkillExtractor({})) == ([], Counter())
    assert api_units_used("theirstack", day_dir) == 0
    assert "2 requests, 0 postings, 0 credits" in capsys.readouterr().out


class RecordingConn:
    """Stands in for the database: records the statements load() sends."""

    def __init__(self):
        self.sql = []

    def transaction(self):
        return nullcontext()

    def cursor(self):
        conn = self

        class Cursor:
            rowcount = 0

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=None):
                conn.sql.append(sql)
        return Cursor()


@pytest.mark.parametrize("source, ok_companies, full_inventory", [
    ("theirstack", None, False), ("greenhouse", ["Acme"], False), ("lever", ["Acme"], False), ("ashby", ["Acme"], False),
    ("recruitee", ["Acme"], False), ("personio", ["Acme"], False), ("remotive", None, True), ("adzuna", None, False),
    ("jooble", None, False)])
def test_empty_fetch_triggers_no_inventory_deactivation(source, ok_companies, full_inventory):
    conn = RecordingConn()
    stats = load(conn, source, [], ok_companies, {}, full_inventory, [])
    assert stats["deactivated"] == 0 and stats["removed"] == 0
    assert not any("NOT (external_id" in s for s in conn.sql)       # "not in today's data" rules didn't run
    assert len(conn.sql) == 1                                       # only the time-based rule


def test_each_source_gets_its_own_open_rule():
    conn = RecordingConn()
    load(conn, "theirstack", [], None, {}, False, [])
    assert "COALESCE(posted_at, first_seen)" in conn.sql[0] and "last_seen" not in conn.sql[0]
    conn = RecordingConn()
    load(conn, "adzuna", [], None, {}, False, [])
    assert "last_seen <" in conn.sql[0] and "posted_at" not in conn.sql[0]


def test_inventory_rule_still_runs_when_the_fetch_had_postings():
    conn = RecordingConn()
    load(conn, "greenhouse", [], ["Acme"], {}, False, ["42"])       # posting present but excluded today
    assert any("company = ANY" in s for s in conn.sql)


def test_open_rule_constants_stay_in_step():
    assert quality_checks.STALE_DAYS == transform_load.STALE_DAYS
    assert quality_checks.ESTIMATED_OPEN_DAYS == transform_load.ESTIMATED_OPEN_DAYS


# ---- pipeline wiring and quality checks ---------------------------------------------------------

def test_pipeline_sources_and_theirstack_args():
    boards = {"greenhouse", "lever", "ashby"}
    assert "theirstack" in run_pipeline.select_sources(None, [], boards)
    assert "theirstack" not in run_pipeline.select_sources(None, ["theirstack", "jooble"], boards)
    cmds = dict(run_pipeline.extract_commands(["theirstack", "adzuna"], ["--max-credits", "15"]))
    assert cmds["theirstack"][-2:] == ["--max-credits", "15"] and "--max-credits" not in cmds["adzuna"]
    assert run_pipeline.THEIRSTACK_MAX_CREDITS["dev"] < run_pipeline.THEIRSTACK_MAX_CREDITS["main"]


class FakeConn:
    """Answers run_checks' two queries: latest run per source, previous loaded count."""

    def __init__(self, runs, previous):
        self.runs, self.previous = runs, previous

    def execute(self, sql, params=None):
        conn = self

        class Cursor:
            def __iter__(self):
                return iter(conn.runs)

            def fetchone(self):
                return (conn.previous.get(params[0]),) if params[0] in conn.previous else None
        return Cursor()


def test_incremental_source_exempt_from_empty_and_drop_checks():
    since = datetime(2026, 11, 3, tzinfo=timezone.utc)
    runs = [("greenhouse", "success", 500, 25, None), ("theirstack", "success", 0, 0, None)]
    results = {r.name: r for r in quality_checks.run_checks(FakeConn(runs, {"greenhouse": 25, "theirstack": 120}),
                                                             since, ["greenhouse", "theirstack"])}
    assert results["sources_nonempty"].ok and results["no_unusual_drop"].ok and results["runs_succeeded"].ok
    # an ordinary source with the same pattern still fails
    runs = [("greenhouse", "success", 0, 0, None)]
    results = {r.name: r for r in quality_checks.run_checks(FakeConn(runs, {"greenhouse": 25}), since, ["greenhouse"])}
    assert not results["sources_nonempty"].ok and not results["no_unusual_drop"].ok


# ---- cursor: a run stopped by a cap never loses postings ----------------------------------------

def fake_api(monkeypatch, tmp_path, postings, balance=None):
    """A TheirStack that honours discovered_at_gte, limit, page and ascending order.
    postings: {query: [iso timestamp, ...]}. Returns the request bodies, for assertions."""
    bodies = []

    class Response:
        status_code = 200

        def __init__(self, data):
            self.data = data
            self.text = '{"data": []}'

        def raise_for_status(self):
            pass

        def json(self):
            return self.data

    class Session:
        headers: dict = {}

        def get(self, *a, **k):
            return Response(balance or {"api_credits": 200, "used_api_credits": 0})

        def post(self, url, json, timeout):
            bodies.append(json)
            found = postings["latam_remote" if "job_location_or" in json else "cr"]
            found = sorted(t for t in found if not json.get("discovered_at_gte")
                           or datetime.fromisoformat(t) >= datetime.fromisoformat(json["discovered_at_gte"]))
            page = found[json["page"] * 25:][:json["limit"]]
            return Response({"data": [{"id": t, "discovered_at": t} for t in page]})

    monkeypatch.setenv("THEIRSTACK_API_KEY", "x" * 20)
    monkeypatch.setattr(ts.requests, "Session", Session)
    monkeypatch.setattr(ts, "RAW_DIR", tmp_path)
    monkeypatch.setattr(ts, "REQUEST_DELAY", 0)
    return bodies


def manifest_of(tmp_path) -> dict:
    return json.loads((next(tmp_path.iterdir()) / "_manifest.json").read_text(encoding="utf-8"))


def stamps(n):
    return [f"2026-11-05T{h:02d}:00:00Z" for h in range(n)]


def test_capped_run_then_next_run_fetches_every_posting(monkeypatch, tmp_path):
    cr = stamps(6)
    bodies = fake_api(monkeypatch, tmp_path, {"cr": cr, "latam_remote": []})
    assert ts.main(["--max-credits", "4", "--queries", "cr", "--since", "2026-11-04T00:00:00Z"]) == 0
    first = manifest_of(tmp_path)
    assert first["queries"][0]["status"] == "capped" and first["queries"][0]["n_jobs"] == 4
    assert first["cursors"]["cr"] == cr[3]              # the last posting fetched, not the date of the run

    del bodies[:]
    assert ts.main(["--max-credits", "50", "--queries", "cr", "--since", f"cr={first['cursors']['cr']}"]) == 0
    second = manifest_of(tmp_path)
    assert bodies[0]["discovered_at_gte"] == cr[3]
    assert second["queries"][0]["status"] == "ok" and second["queries"][0]["n_jobs"] == 3   # cr[3] again, cr[4], cr[5]
    assert second["cursors"]["cr"] != cr[5]             # finished: moves to this run's start (minus overlap)


def test_cap_reached_in_the_first_query_leaves_the_second_cursor_alone(monkeypatch, tmp_path):
    fake_api(monkeypatch, tmp_path, {"cr": stamps(5), "latam_remote": stamps(2)})
    since = ["cr=2026-11-04T00:00:00Z", "latam_remote=2026-11-01T00:00:00Z"]
    assert ts.main(["--max-credits", "5", "--since", *since]) == 0
    assert manifest_of(tmp_path)["cursors"]["latam_remote"] == "2026-11-01T00:00:00Z"


def test_no_credits_at_all_moves_nothing(monkeypatch, tmp_path):
    bodies = fake_api(monkeypatch, tmp_path, {"cr": stamps(3), "latam_remote": []},
                      balance={"api_credits": 200, "used_api_credits": 195})
    assert ts.main(["--since", "2026-11-04T00:00:00Z"]) == 0
    assert bodies == []
    assert set(manifest_of(tmp_path)["cursors"].values()) == {"2026-11-04T00:00:00Z"}


def test_first_run_stopped_before_fetching_stays_a_backfill(monkeypatch, tmp_path):
    fake_api(monkeypatch, tmp_path, {"cr": [], "latam_remote": []},
             balance={"api_credits": 200, "used_api_credits": 195})
    assert ts.main([]) == 0
    assert set(manifest_of(tmp_path)["cursors"].values()) == {None}


def test_free_plan_page_limit_continues_from_the_cursor(monkeypatch, tmp_path):
    many = [f"2026-11-05T{h:02d}:{m:02d}:00Z" for h in range(4) for m in range(60)][:130]
    fake_api(monkeypatch, tmp_path, {"cr": many, "latam_remote": []})
    assert ts.main(["--queries", "cr"]) == 0
    manifest = manifest_of(tmp_path)
    assert manifest["queries"][0]["status"] == "page_limit"
    assert manifest["cursors"]["cr"] == many[124]


def test_latam_query_has_its_own_cap_and_keeps_its_place(monkeypatch, tmp_path):
    latam = stamps(5)
    fake_api(monkeypatch, tmp_path, {"cr": [], "latam_remote": latam})
    assert ts.main(["--since", "2026-11-04T00:00:00Z"]) == 0
    manifest = manifest_of(tmp_path)
    assert manifest["queries"][1]["n_jobs"] == ts.QUERY_MAX_CREDITS["latam_remote"]
    assert manifest["cursors"]["latam_remote"] == latam[2]


def test_cursor_stays_put_if_results_are_not_oldest_first(monkeypatch, tmp_path):
    """order_by is deprecated: if TheirStack stops honouring it, a stopped run must not skip anything."""
    fake_api(monkeypatch, tmp_path, {"cr": [], "latam_remote": []})
    newest_first = ["2026-11-05T09:00:00Z", "2026-11-05T08:00:00Z", "2026-11-05T07:00:00Z"]
    real_post = ts.requests.Session.post

    def post(self, url, json, timeout):
        response = real_post(self, url, json, timeout)
        response.data = {"data": [{"id": t, "discovered_at": t} for t in newest_first][:json["limit"]]}
        return response
    monkeypatch.setattr(ts.requests.Session, "post", post)
    assert ts.main(["--max-credits", "2", "--queries", "cr", "--since", "2026-11-04T00:00:00Z"]) == 1
    manifest = manifest_of(tmp_path)
    assert manifest["queries"][0]["status"] == "error"
    assert manifest["cursors"]["cr"] == "2026-11-04T00:00:00Z"


def test_latam_uses_the_location_id_filter_not_the_deprecated_pattern():
    body = ts.build_body("latam_remote", 0, 25, None)
    assert body["job_location_or"] == [{"id": ts.LATAM_REGION_ID}] and "job_location_pattern_or" not in body
    assert body["order_by"] == [{"field": "discovered_at", "desc": False}]


class OneRow:
    def __init__(self, row):
        self.row = row

    def execute(self, sql):
        return self

    def fetchone(self):
        return self.row


def test_since_values_passed_to_the_extractor():
    started = datetime(2026, 11, 3, 23, 36, tzinfo=timezone.utc)
    assert run_pipeline.theirstack_since(OneRow(None)) == []
    assert run_pipeline.theirstack_since(OneRow((None, started))) == ["2026-11-03T22:36:00Z"]    # before migration 11
    assert run_pipeline.theirstack_since(OneRow(({"cr": "2026-11-05T01:00:00Z", "latam_remote": None}, started))) \
        == ["cr=2026-11-05T01:00:00Z"]                                                           # latam: backfill


# ---- one source failing doesn't stop the others --------------------------------------------------

def test_a_source_with_unreadable_raw_files_does_not_stop_the_others(monkeypatch, tmp_path, capsys):
    finished = {}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            return self

        def fetchall(self):
            return []

        def transaction(self):
            return nullcontext()

    def read(source, day_dir):
        if source == "lever":
            raise FileNotFoundError("_manifest.json")
        return [], ["Acme"], 0, False

    runs = iter(range(1, 10))
    monkeypatch.setattr(transform_load.psycopg, "connect", lambda *a, **k: Conn())
    monkeypatch.setenv("DATABASE_URL", "postgresql://example")
    monkeypatch.setattr(transform_load, "check_role_families", lambda conn: None)
    monkeypatch.setattr(transform_load, "latest_day_dir", lambda source, day: tmp_path)
    monkeypatch.setattr(transform_load, "read_raw", read)
    monkeypatch.setattr(transform_load, "start_run", lambda conn, source: next(runs))
    monkeypatch.setattr(transform_load, "load", lambda *a: Counter())
    monkeypatch.setattr(transform_load, "remove_aggregator_copies", lambda conn: 0)
    monkeypatch.setattr(transform_load, "finish_run",
                        lambda conn, run_id, status, *a, **k: finished.setdefault(run_id, status))
    monkeypatch.setattr(sys, "argv", ["transform_load", "--sources", "greenhouse", "lever", "ashby"])

    assert transform_load.main() == 1                                          # the run is reported as failed...
    assert finished == {1: "success", 2: "failed", 3: "success"}               # ...but ashby, after lever, still loaded
    assert "[lever] FAILED" in capsys.readouterr().out

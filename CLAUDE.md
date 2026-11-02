# SkillMap: project context for Claude Code

## What this is
Portfolio project by Luciano Malagola (Electronic Engineer, data analysis / BI focus).
SkillMap tracks job postings for data roles in **Costa Rica** and **US-remote**, extracts the
tools each posting mentions (Excel, SQL, Power BI, Python…), and ranks them by demand per
role, country and work modality (on-site / hybrid / remote). It must refresh automatically
every day. Key feature: "How well do my skills match?" (match % per role + what to learn next).

Target roles (MVP): Data Analyst, BI Analyst, Business Operations Analyst, Data Reporting.

## Environment
- Developer machine: **Windows, PowerShell**. Repo at `C:\Users\lmala\SkillMap`.
- Python + `requirements.txt` (requests, psycopg[binary], python-dotenv).
- Secrets live only in `.env` (git-ignored). Template: `.env.example`.
  Keys: `DATABASE_URL`, `JOOBLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`.
  **Never commit `.env`, never print keys in output or commit messages.**
- When creating `.env` from PowerShell use `Set-Content -Encoding ascii` (not `>`, which writes UTF-16).

## Architecture
```
GitHub Actions (daily cron)
  → Python ETL (etl/)
  → PostgreSQL on Neon
       ├→ Google Data Studio (live public dashboard; free)
       └→ Power BI Desktop (.pbix in dashboards/; free license, cannot publish online)
```
Note: Google renamed Looker Studio back to **Data Studio** in April 2026. Use "Data Studio".

## Database (Neon)
- Project `skillmap`, id `autumn-pine-31779902`, region aws-us-east-1, PostgreSQL 18.6, database `skillmap`.
- Branch `main`: production, read by dashboards. Branch `dev` (`br-polished-grass-b790an0o`): development/testing.
- **Always develop and test against `dev`.** Only point at `main` when the pipeline is ready.
- Schema: `sql/01_schema.sql`, seed dictionary: `sql/02_seed_skills.sql` (applied to both branches).
  `sql/07_views_90_day_window.sql`: views use **"seen in last 90 days"** (`last_seen >= now() - 90 days`)
  plus **"open now"** (`is_active`); column names carry the window (`_90d` / `_open_now`) and every
  column has a COMMENT with its dashboard label. Tool views (`v_skill_demand`, `v_skill_cooccurrence`):
  full descriptions only. Volume views (`v_modality_mix`, new `v_posting_counts`): all sources: **dev only**.
  `sql/06_ats_sources.sql`: `source` CHECK + 'recruitee', 'personio': **dev only**.
  `sql/05_views_full_text.sql`: `v_skill_demand` and `v_skill_cooccurrence` use only
  `description_is_full = TRUE` (numerator and denominator); `v_modality_mix` keeps all rows: **dev only**.
  `sql/04_seed_web_analytics.sql` (Google Analytics, Adobe Analytics, Google Tag Manager; no `gtm`
  synonym: it means go-to-market in ads): **dev only**.
  `sql/03_market_and_exclusions.sql`: **applied to `dev` only** (2026-11-01); apply to `main` when the
  pipeline is ready. Re-runnable, single transaction.
  Tables: `jobs`, `skills`, `skill_synonyms`, `job_skills`, `etl_runs`.
  Views (after 07): `v_skill_demand`, `v_skill_cooccurrence`, `v_modality_mix`, `v_posting_counts`.
- `jobs` has `UNIQUE(source, external_id)`, `first_seen`/`last_seen`, `is_active`, `description_is_full`,
  `modality` in ('remote','hybrid','onsite','unspecified'), `market` in ('CR','US-remote','LATAM-remote')
  NOT NULL, `source` in ('jooble','adzuna','remotive','greenhouse','lever','ashby').
  Migration 03 **replaced `country` with `market`** (country had no honest value for LATAM-remote and
  duplicated market otherwise; raw location text stays in `location_raw`).
- `etl_runs` has `jobs_excluded` INT and `exclusion_reasons` JSONB (default `{}`).

## Data sources: decisions and findings
- **Core problem:** aggregator APIs return truncated descriptions (~250–300 chars), too short
  to extract tools reliably. So sources are split:
  - **Full descriptions → tool demand:** public ATS job-board endpoints (Greenhouse, Lever, Ashby)
    for a curated list of companies hiring data roles in Costa Rica or US-remote, plus Remotive (remote;
    currently a 12-job sample, see Status).
    These endpoints are official and public. Do NOT scrape LinkedIn or Jooble description pages.
  - **Snippets → market volume** (counts, modality mix, salaries): Jooble, Adzuna.
    Mark these rows `description_is_full = FALSE`.
- **Jooble:** key works on `https://jooble.org/api/` (now the script default), but that endpoint is
  US-only; searching location "Costa Rica" returns arbitrary US results. `cr.jooble.org` returns 403.
  An email was sent (2026-11-01) asking Jooble to enable Costa Rica access and asking about full
  descriptions. Default limit: 500 requests total. Keyword matching is loose (searching
  "data analyst" returned civil engineers), so the transform step needs a **title filter**.
  Jooble response fields: title, location, snippet (HTML entities/<b> tags), salary (free text),
  source, type, link, company, updated, id.
- **Adzuna:** active (keys in `.env`). `etl/extract_adzuna.py`: US only (no Costa Rica coverage), role in
  the title (`title_only`) + `what=remote` (no remote filter exists), `max_days_old=7`, 50/page.
  Default 4 roles × 2 pages (decided 2026-11-02); roles with <50 results stop after page 1, so ~6
  calls/run (~180/month; free tier ~1,000; `--max-calls` caps a run).
  Snippets are cut at 500 chars → `description_is_full = FALSE`. Most salaries are Adzuna estimates
  (`salary_is_predicted = "1"`): only real ones are stored. **Adzuna puts the API app_id in every
  `redirect_url` (`utm_source`)**: it is redacted before the raw file is saved, and the transform also
  strips `utm_*` before storing the URL. Many ads are copied per city (Eliassen: 31 copies) → `duplicate`.
  Terms: personal/academic research; credit "The Adzuna API" with a link to adzuna.com.
  First run (2026-11-01): 4 calls, 130 postings → 24 loaded.
- **USAJOBS:** decided not to use for now.

### Source compliance (rule; follow it for every source)
- Only use sources that offer an **official or documented public API/feed meant for this use**, OR whose
  **terms of service explicitly allow automated access**. If terms prohibit scraping/automated extraction,
  or content requires login, do NOT use the source, whatever technical access exists.
- **Which rules govern what** (clarified 2026-11-02):
  - **Documented API endpoints** (Greenhouse/Lever/Ashby job-board APIs, Recruitee, Personio, Adzuna,
    Jooble, Remotive): governed by the **API's own documentation and terms**: its stated rate limits,
    request quotas, attribution and usage restrictions. Follow them exactly. A robots.txt that disallows
    the API host does not override documented API terms: robots.txt is for crawling web pages.
  - **Web pages / crawling** (any HTML page, sitemap, or undocumented endpoint): governed by **robots.txt**
    (incl. `Crawl-delay`) **and** the site's terms. We don't crawl pages for postings.
  - In both cases, if a robots.txt sets a `Crawl-delay` on a host we call, honour it anyway (Lever: 1 s).
- Check the docs/terms **and** robots.txt before writing any extractor, and record both.
- Never bypass logins, CAPTCHAs or rate limits; never crawl pages that robots.txt disallows.
- Discovering company **names** from public directories by reading them is fine; extracting postings from
  sites that prohibit it is not.
- `docs/SOURCES.md` is the compliance register: for each source considered, the terms URL, robots.txt
  result, decision (allowed / rejected), reason, and date checked. Update it before adding a source.
- Postings can contain personal data (recruiter names/emails). Dashboards show **aggregates only**; never
  publish raw descriptions.

### Location scope and filtering
- **Extract saves EVERYTHING:** the full, unfiltered API response goes to `data/raw/<source>/<date>/`.
  That's the safety net to reprocess without spending API calls. No location filtering at extract time.
  **Only exception: credentials.** Every key-based extractor passes the body through `etl/redact.py`,
  which replaces any `.env` key value with `[REDACTED:<NAME>]` (Adzuna echoes the app_id in every
  `redirect_url`). Raw files become public GitHub Actions artifacts, so they must never hold a key.
- **Transform classifies each posting's location into a market** (`etl/location.py`):
  - `'CR'`: located in Costa Rica (on-site, hybrid or remote).
  - `'US-remote'`: remote postings open to US-based workers.
  - `'LATAM-remote'`: remote postings open to Latin America / Americas / Costa Rica
    (very relevant: Luciano is job hunting from Costa Rica).
  - out of scope: everything else (on-site/hybrid outside CR, remote restricted to other countries).
  - One market per posting. If several apply, precedence is **CR > LATAM-remote > US-remote**
    (most specific location first, then "can someone in Costa Rica apply?").
- **Load inserts ONLY in-scope postings** into Neon. Out-of-scope postings are never loaded.
- **Ambiguous locations** (e.g. "Remote" with no country): keep only if the title/description
  mentions US, LATAM/Latin America/Americas, or Costa Rica; otherwise exclude with reason
  `ambiguous_location`.
- **Each run logs exclusions** in `etl_runs` (`jobs_excluded`, `exclusion_reasons` JSONB, e.g.
  `{"out_of_scope": 120, "ambiguous_location": 15, "title_filter": 40}`).
- **GitHub Actions:** `data/raw/` is temporary on the runner, so the workflow uploads it as a
  **compressed artifact with 3–5 days retention** for debugging (`actions/upload-artifact`,
  `retention-days: 5`, zip compression level 6 = its default).
  - Size: one day of raw data = 83.5 MB uncompressed → **6.9 MB zipped** (measured 2026-11-01,
    ATS boards + Jooble). 5 days retained ≈ 35 MB.
  - GitHub limits (checked 2026-11-01): standard GitHub-hosted runners are free in public repos.
    Artifact storage on GitHub Free is **500 MB** (shared with Packages; Support cannot raise it).
    The official docs don't say whether public-repo artifacts count against it; plan as if they
    do (35 MB is well within). Retention for public repos is configurable 1–90 days (default 90),
    per a third-party guide, not confirmed in the official docs. Re-check if the workflow starts
    failing on storage.

## Status
- [x] Repo, README, schema, seed dictionary, Neon project + branches
- [x] `etl/extract_jooble.py` (saves raw JSON to `data/raw/jooble/<date>/`)
- [x] Company list: `etl/company_candidates.csv` (68 researched, incl. not-found) →
      `etl/discover_boards.py` → `etl/companies.csv` (48 confirmed: 20 CR, 28 US-remote, 8 healthcare).
      Findings: big CR shared-services centres (Intel, P&G, Boston Scientific, Experian…) use
      Workday/SuccessFactors, so they're unreachable via these APIs; the CR list is tech/nearshore.
      Only ~60 titles across all boards look like target roles: value is full descriptions, not volume.
      `market` is the company's market; big US boards also carry on-site/overseas jobs.
- [x] `sql/03_market_and_exclusions.sql` applied to `dev` (not `main`): `jobs.market` replaces
      `country`, ATS sources allowed, `etl_runs` exclusion columns, views grouped by market.
- [x] `etl/location.py`: `classify_posting(source, job, description=None) -> (market, reason)` for
      raw postings (use this in transform; pass the cleaned description), built on
      `classify_location(...)` for plain fields. EN/ES. Structured fields preferred: Lever
      `workplaceType` + `country` (primary location only), Ashby `workplaceType` (over `isRemote`) +
      `addressCountry` per location; regions in the text are still added, explicit "Costa Rica"
      always counts. Greenhouse `offices` is often an HQ, so it's used only for bare "Remote"
      after the title and before the description. `tests/test_location.py` (138 cases: real
      strings + 38 real postings in `tests/fixtures/real_postings.json`); run
      `python -m pytest tests/` (dev deps: `requirements-dev.txt`). Known limit: free-text
      restrictions inside descriptions ("must be based in Europe") aren't parsed.
      Rules beyond the spec: remote restricted to ONE non-CR
      LATAM country is out of scope; 2+ LATAM countries or "Anywhere"/"Worldwide" → LATAM-remote;
      country-level "United States" with no remote flag → US-remote only if the description says
      remote/distributed; "Remote" + 2+ US state names in the text → US-remote.
- [x] CR company expansion (2026-11-02): 149 more candidates from CINDE news, job fairs, the Coyol /
      Pharmchoices medical-device directories and a CR hiring tracker; probed on the 5 allowed ATSs.
      Confirmed 5 new CR boards, all Greenhouse: Power Digital, VML (`wundermanthompson`), Smartsheet,
      OfficeSpace Software, Greenlight Consulting → `etl/companies.csv` = 53 boards (25 CR). Medical-device
      and shared-services companies are on Workday/SuccessFactors (rejected) → none found. Candidates whose
      slug hit a different company or whose board has no CR postings are kept with `market = excluded`
      (skipped by `discover_boards.py`). Discovery rate limit is per platform domain (Personio returned 429
      when limited per subdomain).
- [x] Recruitee (`{co}.recruitee.com/api/offers/`) and Personio (`{co}.jobs.personio.de/xml`) supported in
      discovery, extraction and transform (`normalize.parse_board` handles every ATS format, incl.
      Personio XML). Recruitee: remote/hybrid/on_site booleans + country_code; Personio: office text only.
- [x] `etl/extract_ats.py`: all boards in `etl/companies.csv`, raw bodies saved byte-for-byte to
      `data/raw/<ats>/<date>/<token>.json` + `_manifest.json` (status / n_jobs / file per board;
      partial re-runs merge into it). Sequential, 1 s between boards, retries on network/429/5xx;
      a failed board is recorded and the run continues (exit 1 only if every board fails).
      First run (2026-11-01): 48/48 boards, 5,400 postings, 83.5 MB, ~80 s. Verified: counts match
      Greenhouse `meta.total`, Lever returns everything without pagination, median description
      3.7k–6.4k chars.
- [x] `etl/extract_adzuna.py` + Adzuna in transform (see Data sources). Snippet sources (Jooble,
      Adzuna) get remote/not from title, location, snippet (`location._snippet_workplace`).
- [x] `etl/extract_remotive.py` + Remotive in transform. Categories `data` and `operations` = 2 requests
      per run, 35 s apart (terms: max ~4/day, >2/min blocked; link back to the Remotive URL and name
      Remotive as source; don't resubmit jobs to other boards). Full HTML descriptions →
      `description_is_full = TRUE`; always remote; `candidate_required_location` is the location;
      salary parsed from text ($ only, monthly ×12, hourly/per-task/OTE skipped). Each category
      response is a complete list → if all categories OK, missing postings are deactivated.
      **Finding (2026-11-01): the free public API returns a fixed sample of 12 jobs and ignores
      `category` and `search`** (same 12 IDs for data, operations and search=analyst; none are data
      roles). Full feed = private paid API (from $5k/month). Kept running (cheap, within terms) in
      case filtering returns; it currently loads 0 rows.
- [x] `etl/transform_load.py` (+ `etl/normalize.py`, `etl/roles.py`, `etl/skills.py`; tests in
      `tests/test_transform.py`). Latest raw day per source; ATS via `_manifest.json` (only `ok` boards).
      Exclusions counted once, first reason wins, in funnel order: location (`out_of_scope` /
      `ambiguous_location`) → `title_filter` → `no_text` → `placeholder` ("TBD" or <200 chars left after
      removing legal/HR boilerplate, calibrated on 5,400 postings: placeholders had 3 and 24, shortest
      real posting 316; full-text sources only) → `duplicate` (same company+title+description;
      smallest `external_id` kept). Upsert on (source, external_id); `job_skills` replaced each run.
      Deactivation: boards fetched OK → their jobs not loaded today; any source → not seen for 14 days
      (Jooble isn't a full inventory). One `etl_runs` row per source, own transaction per source.
      `--dry-run` writes nothing. Decisions:
      - Roles are narrow (see `roles.py` docstring): no Data Scientist/Engineer, Business Analyst,
        finance/risk/payroll/security analysts, generic Operations Analyst. Precedence BI > Reporting >
        BizOps > Data Analyst.
      - Tools: longest synonym first + masking ("Looker Studio" ≠ Looker; "SQL Server" ≠ SQL);
        context rules for excel (verb), epic (EHR only), macros, pbi; standalone "R" detected in code
        with programming context (rejects "R$", "R&D", "(R)", "Pay Grade - R").
      - Modality: remote markets → remote; else Lever/Ashby workplaceType, then location, title, description.
      - Salary annualised (month ×12, bi-week ×26); hourly skipped. Greenhouse from the `pay-range` block.
      First load to `dev` (2026-11-01): **12 postings** (7 CR, 5 US-remote, 0 LATAM-remote) out of 5,417.
      Finding: most data-analyst roles at these US companies are hybrid/on-site in hubs (Garner, Brex,
      Mozilla Europe, GitLab Bangalore) and drop out by location; volume will come from more sources.
- [ ] GitHub Actions daily workflow (secrets: `DATABASE_URL` for main, API keys); upload
      `data/raw/` as a compressed artifact, `retention-days: 5` (~7 MB/day zipped)
- [ ] Data Studio dashboard (public link) + Power BI `.pbix` with "How well do I match?" page
- [x] README: architecture, sources, three markets, data model, limitations, credits (Adzuna, Remotive, Jooble)
- [ ] README: findings with numbers and dashboard screenshots (after the dashboards)

## Working conventions
- Talk with Luciano in the language he writes in (Spanish or English).
- Explain choices briefly; he is building this for his portfolio and wants to understand it.
- Small, focused commits with clear messages. Push to `main` (solo project).
- Test every script against real API responses before building on top of it.
- Be polite to APIs: sleep between requests, respect rate limits.

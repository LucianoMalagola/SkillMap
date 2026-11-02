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
  `sql/03_market_and_exclusions.sql`: **applied to `dev` only** (2026-11-01); apply to `main` when the
  pipeline is ready. Re-runnable, single transaction.
  Tables: `jobs`, `skills`, `skill_synonyms`, `job_skills`, `etl_runs`.
  Views: `v_skill_demand`, `v_modality_mix` (both by `market`), `v_skill_cooccurrence`.
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
    for a curated list of companies hiring data roles in Costa Rica or US-remote, plus Remotive (remote).
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
- **Adzuna:** account in "Pending Activation". Free tier ~1,000 calls/month. No Costa Rica coverage.
- **USAJOBS:** decided not to use for now.

### Location scope and filtering
- **Extract saves EVERYTHING:** the full, unfiltered API response goes to `data/raw/<source>/<date>/`.
  That's the safety net to reprocess without spending API calls. No location filtering at extract time.
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
- **GitHub Actions:** `data/raw/` is temporary on the runner, so the workflow uploads it as an
  artifact (short retention) for debugging.

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
- [x] `etl/location.py`: `classify_location(location, title, description, workplace_type=,
      is_remote=, country_code=) -> (market, reason)`, EN/ES. `tests/test_location.py` (94 cases,
      real strings from Jooble raw + 16 ATS boards); run `python -m pytest tests/`
      (dev deps: `requirements-dev.txt`). Rules beyond the spec: remote restricted to ONE non-CR
      LATAM country is out of scope; 2+ LATAM countries or "Anywhere"/"Worldwide" → LATAM-remote;
      country-level "United States" with no remote flag → US-remote only if the description says
      remote/distributed; "Remote" + 2+ US state names in the text → US-remote.
- [x] `etl/extract_ats.py`: all boards in `etl/companies.csv`, raw bodies saved byte-for-byte to
      `data/raw/<ats>/<date>/<token>.json` + `_manifest.json` (status / n_jobs / file per board;
      partial re-runs merge into it). Sequential, 1 s between boards, retries on network/429/5xx;
      a failed board is recorded and the run continues (exit 1 only if every board fails).
      First run (2026-11-01): 48/48 boards, 5,400 postings, 83.5 MB, ~80 s. Verified: counts match
      Greenhouse `meta.total`, Lever returns everything without pagination, median description
      3.7k–6.4k chars.
- [ ] Remotive extractor (remote postings, full descriptions).
- [ ] **Next:** `etl/transform_load.py`. Read ATS files via each `_manifest.json`; **only deactivate
      stale jobs for boards with status `ok` that day** (a failed fetch is not a closed job).
      Format notes (samples in `data/raw/samples/`):
      - Greenhouse: `content` is double-escaped HTML (`html.unescape` first).
      - Lever: requirements often live in `lists[]` ("Who You Are"), not `descriptionPlain`;
        concatenate description + lists + additional. `descriptionPlain` can be empty while the
        HTML `description` is full (CSC "Seasonal Custom Boot Fitter"): fall back to stripping
        `description`. 2 CSC postings have no text at all. Gives `workplaceType` and `country`.
      - Ashby: `descriptionPlain`, `isRemote` (unreliable: trust `workplaceType`),
        `secondaryLocations`, `compensation`.
      - Bluelight posts one copy per location (612 postings, 9 titles): `etl/location.py` keeps only
        the Costa Rica copies; still dedupe by title + description.
      Then: HTML cleanup, title/role filter, market classification via
      `etl/location.py` (load only in-scope postings), tool extraction via `skill_synonyms`
      (regex with word boundaries; beware false positives like "R" and "Epic"), bilingual modality
      classification (remote/remoto/teletrabajo, hybrid/híbrido, on-site/presencial), upsert with
      `ON CONFLICT (source, external_id) DO UPDATE SET last_seen = now()`, deactivate stale jobs,
      log each run in `etl_runs` incl. `jobs_excluded` and `exclusion_reasons`
- [ ] GitHub Actions daily workflow (secrets: `DATABASE_URL` for main, API keys); upload
      `data/raw/` as a short-retention artifact (~85 MB/day from the ATS boards alone)
- [ ] Data Studio dashboard (public link) + Power BI `.pbix` with "How well do I match?" page
- [ ] README: findings with numbers, screenshots, limitations

## Working conventions
- Talk with Luciano in the language he writes in (Spanish or English).
- Explain choices briefly; he is building this for his portfolio and wants to understand it.
- Small, focused commits with clear messages. Push to `main` (solo project).
- Test every script against real API responses before building on top of it.
- Be polite to APIs: sleep between requests, respect rate limits.

# SkillMap

**Which tools do employers actually ask for?** SkillMap tracks job postings for data roles (Data Analyst, BI Analyst, Business Operations Analyst, Data Reporting) open to people in **Costa Rica**, extracts the tools each posting mentions (Excel, SQL, Power BI, Python…), and ranks them by demand per role, market and work modality (on-site, hybrid, remote).

It covers three markets:

| Market | What it means |
|---|---|
| **CR** | Located in Costa Rica: on-site, hybrid or remote |
| **US-remote** | Remote, open to US-based workers |
| **LATAM-remote** | Remote, open to Latin America / the Americas / worldwide (so someone in Costa Rica can apply) |

The pipeline is built to refresh automatically every day.

> 🚧 **Status:** in development. Extraction, transformation and loading work end to end against a development database; the daily GitHub Actions workflow and the dashboards are next.

## Questions it answers

- For a *Data Analyst* role, which tools appear in the most postings?
- Do remote jobs ask for different tools than on-site ones?
- How common are hybrid and remote roles in Costa Rica, and how many remote roles are open to Latin America?
- If a posting asks for Power BI, what else does it usually ask for?
- **How well do my skills match each role, and what should I learn next?**

## Architecture

```
GitHub Actions (daily cron)
  → Extract: every source's full, unfiltered API response saved to data/raw/<source>/<date>/
       ├ ATS job boards (Greenhouse, Lever, Ashby): 48 companies, full descriptions
       ├ Adzuna: US remote postings, snippets (market volume)
       ├ Jooble: snippets (market volume)
       ├ Remotive: remote postings, full descriptions
       └ TheirStack: Costa Rica + LATAM-remote postings from employer career sites / ATS pages
  → Cross-source deduplication: the same posting from an ATS board and an aggregator is kept once (ATS copy)
  → Transform: HTML cleanup → location → market (CR / US-remote / LATAM-remote) → title filter
               → deduplication → modality → tool extraction
  → Load: only in-scope postings, upserted into PostgreSQL on Neon; each run logged with
          exclusion counts by reason
       ├→ Google Data Studio   (live public dashboard)
       └→ Power BI Desktop     (in-depth analysis, .pbix in /dashboards)
```

## Data sources

Aggregator APIs return descriptions cut to a few hundred characters, too short to count tools reliably. So sources are split by what they're good for:

| Source | Used for | Description | Notes |
|---|---|---|---|
| **ATS job boards**: Greenhouse, Lever, Ashby, Recruitee, Personio | Tool demand | Full | Official public job-board endpoints for a curated list of 48 companies hiring in Costa Rica or remotely (`etl/companies.csv`, built and verified by `etl/discover_boards.py`) |
| **Adzuna** | Market volume | Snippet | US postings that mention remote work |
| **Jooble** | Market volume | Snippet | Its API currently serves US results only |
| **Remotive** | Tool demand | Full | Remote-only board; its free API currently returns a small fixed sample |
| **TheirStack** | Costa Rica coverage | Full (measured per posting) | Postings for our roles in Costa Rica and LATAM-remote, **only those with the employer's own career-site or ATS page** (no LinkedIn/Indeed-only listings). Free plan: 200 credits a month (1 per posting), so each run is capped and fetches only postings discovered since the previous run |

Snippet-based rows are flagged `description_is_full = FALSE`, so tool rankings can be restricted to full descriptions. For TheirStack the flag is decided from the text itself (at least 700 characters of substantive text, not cut off with "…").

When the same posting arrives from an ATS board and an aggregator (the aggregator's link carries the ATS posting id, or same company and title), only the ATS copy is loaded; the aggregator copy is counted as `duplicate_ats` in `etl_runs`. Compliance decisions for every source considered are in [docs/SOURCES.md](docs/SOURCES.md).

**How a posting's market is decided** (`etl/location.py`): structured fields first (Lever/Ashby work type and country), then the location text, then the title, and the description only as a last resort. It handles English and Spanish ("Remoto", "Teletrabajo", "San José, Costa Rica"). Postings that fit no market are excluded and counted, never loaded.

## Data model

| Table | Purpose |
|---|---|
| `jobs` | One row per posting: title, company, **market**, modality, role, salary (annual), description, `first_seen` / `last_seen`, `is_active` |
| `skills` | Canonical tool catalog (e.g. *Power BI*) with a category |
| `skill_synonyms` | Every spelling that maps to a tool (*powerbi*, *pbi* → Power BI) |
| `job_skills` | Bridge table: which tools each posting mentions |
| `etl_runs` | Log of each pipeline run per source, including how many postings were excluded and why (powers the "last updated" indicator) |

Dashboard views. Two time windows, named in every column:

- **Last 90 days** (`*_90d`): postings seen at least once in the last 90 days, including ones that have since closed. Used for tool demand, so percentages reflect a quarter rather than one day.
- **Open now** (`*_open_now`): postings still listed as of the latest run.

| View | What it shows | Sources |
|---|---|---|
| `v_skill_demand` | % of postings asking for each tool, per role / market / modality | Full descriptions only |
| `v_skill_cooccurrence` | Tools asked for together | Full descriptions only |
| `v_modality_mix` | On-site / hybrid / remote mix per market | All sources |
| `v_posting_counts` | Headline counts per market and role (90 days vs open now) | All sources |

Each column carries a database `COMMENT` with its intended dashboard label.

## Repository structure

```
sql/          schema, seed data and migrations (01–04)
etl/          extraction, transformation and loading scripts
tests/        pytest suite built from real postings (tests/fixtures/)
dashboards/   Power BI file and screenshots
data/raw/     raw API responses (not committed)
```

## How to run and test

Windows PowerShell, from the repository root. One-time setup:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
Copy-Item .env.example .env      # then fill in the values (dev connection string, API keys)
```

`etl.run_pipeline` is the single entry point, used both locally and by GitHub Actions.
`--target dev` reads `DATABASE_URL_DEV` (or `DATABASE_URL`) from `.env`; `--target main` needs
`DATABASE_URL_MAIN`, which only the workflow has: production is written by the workflow.

```powershell
# Tests (no database or network needed)
python -m pytest tests/

# Lint
python -m ruff check etl tests

# Full pipeline on dev: extract every source, load, run the data-quality checks
python -m etl.run_pipeline --target dev --skip-jooble

# Dry run: extract nothing, transform today's raw files, report, write nothing
python -m etl.run_pipeline --target dev --skip-extract --skip-jooble --dry-run

# Reprocess raw files without calling any API (today's, or a past day)
python -m etl.run_pipeline --target dev --skip-extract --skip-jooble
python -m etl.run_pipeline --target dev --date 2026-11-01 --skip-jooble

# A single source, or everything except some sources
python -m etl.run_pipeline --target dev --sources lever
python -m etl.run_pipeline --target dev --skip jooble theirstack

# TheirStack credits: show this month's balance and the run's cap, spend nothing
python etl/extract_theirstack.py --plan

# Schema: apply pending migrations to dev (main gets them through the workflow)
python -m etl.migrate --target dev --dry-run
python -m etl.migrate --target dev

# Data-quality checks on their own, and regenerate docs/DATA_DICTIONARY.md
python -m etl.quality_checks --target dev
python -m etl.gen_data_dictionary --target dev
```

`--skip-jooble` stays until Jooble enables Costa Rica results for our key. Remotive allows
about 4 requests a day and each full run makes 2, so for repeated local runs prefer
`--skip-extract`.

TheirStack credits (200 a month on the free plan) are shared by every run: dev, main and local.
Each run spends at most `--theirstack-max-credits` (default 15 on dev, 150 on main) and never
goes past the month's credits minus a reserve of 10; reaching a cap is logged and is not a
failure. Each run asks only for postings discovered since the last successful TheirStack run on
that database (the first run on a database backfills 90 days).

## Automation (GitHub Actions)

`.github/workflows/pipeline.yml` runs `python -m etl.run_pipeline --target <dev|main> --migrate --skip-jooble`:

- **Manual** (Actions → *SkillMap pipeline* → *Run workflow*): choose `target` (default `dev`),
  optionally *migrate only*, *include Jooble* or *skip TheirStack*.
- **Daily** at 11:17 UTC (05:17 Costa Rica), always against `main`. Enabled only after a manual
  dev run and a manual main run have passed.
- Raw API responses are uploaded as a zip artifact kept **5 days** (~7 MB a day; GitHub Free
  storage is 500 MB). Keys are redacted before saving and scanned again before upload: the repo
  is public, so artifacts are downloadable by anyone.
- Any failed step or data-quality check fails the run, and GitHub emails the failure.
- `.github/workflows/tests.yml` runs ruff and pytest on every push.

Repository secrets (Settings → Secrets and variables → Actions → *New repository secret*):

| Secret | Value | Where to get it |
|---|---|---|
| `DATABASE_URL_DEV` | Connection string of the Neon `dev` branch, role `skillmap_owner`, database `skillmap` | Neon console → project *skillmap* → **Connect** → Branch `dev`, Database `skillmap`, Role `skillmap_owner` → copy the connection string (pooled is fine) |
| `DATABASE_URL_MAIN` | Same for the `main` branch | Same dialog with Branch `main` |
| `ADZUNA_APP_ID` | Adzuna application ID | [developer.adzuna.com](https://developer.adzuna.com) → *Dashboard* → API Access Details (also in your local `.env`) |
| `ADZUNA_APP_KEY` | Adzuna application key | Same page |
| `JOOBLE_API_KEY` | Jooble API key | The key Jooble emailed you (also in your local `.env`); only used when *include Jooble* is ticked |
| `THEIRSTACK_API_KEY` | TheirStack API key | [app.theirstack.com](https://app.theirstack.com) → Settings → API Key (also in your local `.env`) |

The job only receives the connection string of the target it runs against.

### Schema changes (migrations)

`sql/NN_<name>.sql` files are applied once each, in order, by `etl/migrate.py` and recorded in
the `schema_migrations` table; `sql/R_*.sql` (the read-only dashboard role and its grants) runs
after every migrate. Process: write the migration → `python -m etl.migrate --target dev` →
test on dev → commit and push → the next workflow run (manual or daily) applies it to `main`.
`main` is never migrated from a laptop.

## Tech stack

Python · PostgreSQL (Neon) · SQL · pytest · GitHub Actions · Google Data Studio · Power BI · DAX

## Limitations

- **Volume is small.** Few postings match the four target roles *and* one of the three markets: most data-analyst roles at the tracked companies are hybrid or on-site in US/European hubs. Large Costa Rica shared-services employers recruit through Workday or SuccessFactors, which have no public API, so the Costa Rica side leans on tech and nearshore companies.
- Snippet sources (Adzuna, Jooble) undercount tool mentions; every row is flagged with `description_is_full`.
- The title filter is deliberately narrow (no Data Scientist, Data Engineer or Business Analyst roles), favouring precision over volume.
- Work modality comes from structured fields when available, otherwise from text in English and Spanish; postings with no signal are labelled *unspecified*.
- Restrictions written only inside a description ("must be based in Europe") aren't parsed.

## Credits

- Job data from **[The Adzuna API](https://www.adzuna.com)**.
- Remote job data from **[Remotive](https://remotive.com)**. Each posting links back to its Remotive page.
- Job data from **[Jooble](https://jooble.org)**.
- Costa Rica and LATAM job data from **[TheirStack](https://theirstack.com)**.
- Company job boards via the public **Greenhouse**, **Lever** and **Ashby** job-board APIs.

## Author

**Luciano Malagola**: Electronic Engineer specializing in data analysis and business intelligence.

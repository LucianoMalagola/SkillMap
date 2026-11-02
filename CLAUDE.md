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
- Schema: `sql/01_schema.sql`, seed dictionary: `sql/02_seed_skills.sql` (already applied to both branches).
  Tables: `jobs`, `skills`, `skill_synonyms`, `job_skills`, `etl_runs`.
  Views: `v_skill_demand`, `v_skill_cooccurrence`, `v_modality_mix`.
- `jobs` has `UNIQUE(source, external_id)`, `first_seen`/`last_seen`, `is_active`,
  `description_is_full`, `modality` in ('remote','hybrid','onsite','unspecified'), `country` in ('CR','US').
  The `source` CHECK currently allows only ('jooble','adzuna','remotive'); extend it
  (via a new migration file, e.g. `sql/03_...sql`) when adding greenhouse/lever/ashby.

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

## Status
- [x] Repo, README, schema, seed dictionary, Neon project + branches
- [x] `etl/extract_jooble.py` (saves raw JSON to `data/raw/jooble/<date>/`)
- [ ] **Next:** build the company list. Research companies with data roles in Costa Rica and
      US-remote that use Greenhouse / Lever / Ashby; write `etl/discover_boards.py` to verify each
      board token responds and has open postings; save confirmed list to `etl/companies.csv`
      (columns: company, ats, board_token, market).
- [ ] ATS extractors (Greenhouse, Lever, Ashby) + Remotive extractor
- [ ] `etl/transform_load.py`: HTML cleanup, title/role filter, tool extraction via `skill_synonyms`
      (regex with word boundaries; beware false positives like "R" and "Epic"), bilingual modality
      classification (remote/remoto/teletrabajo, hybrid/híbrido, on-site/presencial), upsert with
      `ON CONFLICT (source, external_id) DO UPDATE SET last_seen = now()`, deactivate stale jobs,
      log each run in `etl_runs`
- [ ] GitHub Actions daily workflow (secrets: `DATABASE_URL` for main, API keys)
- [ ] Data Studio dashboard (public link) + Power BI `.pbix` with "How well do I match?" page
- [ ] README: findings with numbers, screenshots, limitations

## Working conventions
- Talk with Luciano in the language he writes in (Spanish or English).
- Explain choices briefly; he is building this for his portfolio and wants to understand it.
- Small, focused commits with clear messages. Push to `main` (solo project).
- Test every script against real API responses before building on top of it.
- Be polite to APIs: sleep between requests, respect rate limits.

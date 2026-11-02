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
       └ Remotive: remote postings, full descriptions
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
| **ATS job boards**: Greenhouse, Lever, Ashby | Tool demand | Full | Official public job-board endpoints for a curated list of 48 companies hiring in Costa Rica or remotely (`etl/companies.csv`, built and verified by `etl/discover_boards.py`) |
| **Adzuna** | Market volume | Snippet | US postings that mention remote work |
| **Jooble** | Market volume | Snippet | Its API currently serves US results only |
| **Remotive** | Tool demand | Full | Remote-only board; its free API currently returns a small fixed sample |

Snippet-based rows are flagged `description_is_full = FALSE`, so tool rankings can be restricted to full descriptions.

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
- Company job boards via the public **Greenhouse**, **Lever** and **Ashby** job-board APIs.

## Author

**Luciano Malagola**: Electronic Engineer specializing in data analysis and business intelligence.

# SkillMap

**Which tools do employers actually ask for?** SkillMap tracks job postings for data roles in **Costa Rica** and **US-remote** positions, extracts the tools each posting mentions (Excel, SQL, Power BI, Python…), and ranks them by demand, by role, country and work modality (on-site, hybrid, remote).

The data refreshes automatically every day.

> 🚧 **Status:** in development. Database is live; extraction pipeline in progress.

## Questions it answers

- For a *Data Analyst* role, which tools appear in the most postings?
- Do remote jobs ask for different tools than on-site ones?
- How common are hybrid and remote roles in Costa Rica compared with US-remote postings?
- If a posting asks for Power BI, what else does it usually ask for?
- **How well do my skills match each role, and what should I learn next?**

## Architecture

```
GitHub Actions (daily cron)
  → Python ETL: Jooble (Costa Rica) + Adzuna (US) + Remotive (remote)
  → cleaning: deduplication, tool extraction, modality classification
  → PostgreSQL on Neon
       ├→ Google Data Studio   (live public dashboard)
       └→ Power BI Desktop     (in-depth analysis, .pbix in /dashboards)
```

## Data model

| Table | Purpose |
|---|---|
| `jobs` | One row per posting: title, company, country, modality, role, salary, `first_seen` / `last_seen` |
| `skills` | Canonical tool catalog (e.g. *Power BI*) with a category |
| `skill_synonyms` | Every spelling that maps to a tool (*powerbi*, *pbi* → Power BI) |
| `job_skills` | Bridge table: which tools each posting mentions |
| `etl_runs` | Log of each pipeline run (powers the "last updated" indicator) |

Dashboard views: `v_skill_demand`, `v_skill_cooccurrence`, `v_modality_mix`.

## Repository structure

```
sql/          schema and seed data
etl/          Python extraction and loading scripts
dashboards/   Power BI file and screenshots
data/raw/     raw API responses (not committed)
```

## Tech stack

Python · PostgreSQL (Neon) · SQL · GitHub Actions · Google Data Studio · Power BI · DAX

## Limitations

- Some APIs return truncated job descriptions, which can undercount tool mentions. Each posting is flagged with `description_is_full`.
- Work modality is inferred from posting text in English and Spanish; postings with no signal are labeled *unspecified*.

## Author

**Luciano Malagola**: Electronic Engineer specializing in data analysis and business intelligence.

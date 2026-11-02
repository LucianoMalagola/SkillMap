# SkillMap data dictionary

The **views are the contract with the dashboards** (Data Studio, Power BI). Columns are only
ever added; renaming or removing one is a breaking change and is announced first.
`docs/view_contract.json` lists the contracted columns and every pipeline run checks they still
exist (`etl/quality_checks.py`, check `view_contract`).

This file is generated from the database comments: `python -m etl.gen_data_dictionary --target dev`.
Edit labels in a migration (`COMMENT ON ...`), not here. The text before "## Views" is kept in
`etl/gen_data_dictionary.py`.

## Conventions

| Term | Meaning |
|---|---|
| **Last 90 days** (`*_90d`, `seen_90d`) | Postings seen at least once in the last 90 days (`jobs.last_seen >= now() - 90 days`), including ones that have since closed. |
| **Open now** (`*_open_now`, `open_now`) | Still listed as of the latest pipeline run (`jobs.is_active`). |
| **Full description** | `description_is_full = TRUE`: ATS boards and Remotive. Tool views count only these, in numerator **and** denominator. Adzuna/Jooble snippets count only in volume views. |
| **n / enough_data** | `n` = postings in the denominator of the row's percentage. `enough_data = n >= 10`. Dashboards show a percentage only when `enough_data` is true; otherwise show the count or "not enough data". |
| **Market** | `CR` (located in Costa Rica: on-site, hybrid or remote), `US-remote` (remote, open to US-based workers), `LATAM-remote` (remote, open to Latin America / the Americas / worldwide). |
| **Role family** | `Data & Analytics` (Data Analyst, BI Analyst, Data Reporting, Analytics & Insights), `Marketing & Insights` (Marketing Analyst, incl. Marketing Science), `Business & Operations` (Business Operations Analyst, Business Analyst). See `v_roles`. |
| **Weeks** | ISO weeks starting Monday, UTC. |

Personal data: no view exposes the description text (it can contain recruiter names/emails).
The `dashboard_reader` role can read the views below and nothing else.

## Skill match ("How well do my skills match?")

**Definition.** For a segment (role or role family × market), *match %* = share of all tool
requests in the segment's full-description postings (last 90 days) that your tools cover:
`SUM(mentions of your tools) / SUM(mentions of all tools)`. *What to learn next* = the
most-requested tools you don't have (highest `postings_mentioning_90d`).

**SQL / notebooks:**
```sql
SELECT * FROM fn_skill_match(ARRAY['Excel', 'SQL', 'Power BI'], 'role_category');  -- or 'role_family'
```

**Power BI** (import `v_skill_demand_segment` and `v_skills`; keep them **unrelated**: `v_skills`
is a disconnected "My skills" slicer table):
```DAX
-- Page filter: v_skill_demand_segment[grain] = "role_category" (or "role_family")

Match % =
VAR mine   = VALUES ( v_skills[skill_name] )
VAR total  = CALCULATE ( SUM ( v_skill_demand_segment[postings_mentioning_90d] ) )
VAR hit    = CALCULATE ( SUM ( v_skill_demand_segment[postings_mentioning_90d] ),
                         TREATAS ( mine, v_skill_demand_segment[skill_name] ) )
RETURN IF ( ISFILTERED ( v_skills[skill_name] ), DIVIDE ( hit, total ) )

Is Missing =      -- use as a visual-level filter (= 1) on a table of skill_name, ranked by pct_postings_90d
IF ( ISFILTERED ( v_skills[skill_name] ),
     IF ( SELECTEDVALUE ( v_skill_demand_segment[skill_name] ) IN VALUES ( v_skills[skill_name] ), 0, 1 ),
     1 )

Enough Data = IF ( MIN ( v_skill_demand_segment[n] ) >= 10, "yes", "not enough data" )
```
Put `segment` and `market` on rows of a matrix with `Match %`, and always filter `grain` to one value.

**Data Studio:** no parameters for PostgreSQL custom queries, so use a fixed skill list in a
custom query (`SELECT * FROM fn_skill_match(ARRAY['Excel','SQL'])`) or chart
`v_skill_demand_segment` directly.

## Views

| View | Purpose |
|---|---|
| [`v_last_updated`](#v_last_updated) | Freshness per source from etl_runs. pipeline_last_success_at is the "Last updated" value for a dashboard header. |
| [`v_modality_mix`](#v_modality_mix) | Work modality per market, all sources (incl. snippet sources). US-remote and LATAM-remote are remote by definition. |
| [`v_posting_counts`](#v_posting_counts) | Headline counts per market and role. All sources; the *_full_text_* columns are the subset used by the tool views. |
| [`v_posting_skills`](#v_posting_skills) | Long format: one row per (posting, tool). Postings with no tools have no rows here (use v_postings.n_tools). Filter description_is_full = TRUE for tool demand. |
| [`v_postings`](#v_postings) | One row per loaded posting, for EDA. All sources, all time (filter seen_90d / open_now). The description text is deliberately left out: it can contain recruiter names and emails. |
| [`v_roles`](#v_roles) | Role categories and the family each belongs to. Use as a dimension / for sort order. |
| [`v_skill_cooccurrence`](#v_skill_cooccurrence) | Tools asked for in the same posting, directed: one row for (A,B) and one for (B,A). Full-description postings seen in the last 90 days. Filter skill_a to read "postings asking for A that also ask for B". |
| [`v_skill_demand`](#v_skill_demand) | Tool demand per role/market/modality. Full-description postings only (Adzuna/Jooble snippets excluded). Window: postings seen in the last 90 days; *_open_now columns restrict to postings still listed. |
| [`v_skill_demand_segment`](#v_skill_demand_segment) | Tool demand per segment = (role category or role family) x market, all modalities. Full-description postings seen in the last 90 days. Filter grain to one value: rows of the two grains overlap. |
| [`v_skills`](#v_skills) | Every tool in the dictionary, including ones no posting mentions yet. Use as the "My skills" slicer table for the skill-match page. |
| [`v_weekly_postings`](#v_weekly_postings) | Postings per ISO week (Monday start, UTC), market and role. All sources. A posting is "listed" in every week between its first_seen and last_seen. Additive across markets and roles. |
| [`v_weekly_skill_trend`](#v_weekly_skill_trend) | Tool demand per ISO week, market and role family. Full-description postings listed during the week. postings_mentioning and n are additive across markets/families; percentages are not. |

### v_last_updated

Freshness per source from etl_runs. pipeline_last_success_at is the "Last updated" value for a dashboard header.

| Column | Type | Label / meaning |
|---|---|---|
| `source` | text | Label: "Source". |
| `last_success_at` | timestamp with time zone | Label: "Last successful update". |
| `hours_since_success` | numeric | Label: "Hours since last update". |
| `last_success_jobs_fetched` | integer | Label: "Postings fetched (last update)". Before any filtering. |
| `last_success_jobs_loaded` | integer | Label: "Postings loaded (last update)". In scope after filtering; NULL before migration 08. |
| `last_success_jobs_new` | integer | Label: "New postings (last update)". |
| `last_success_jobs_excluded` | integer | Label: "Postings excluded (last update)". Out of scope, not a target role, duplicates... |
| `last_run_at` | timestamp with time zone | Label: "Last run started". |
| `last_run_status` | text | Label: "Last run status". success / failed / running. |
| `pipeline_last_success_at` | timestamp with time zone | Label: "Last updated". Latest successful run across all sources. |

### v_modality_mix

Work modality per market, all sources (incl. snippet sources). US-remote and LATAM-remote are remote by definition.

| Column | Type | Label / meaning |
|---|---|---|
| `market` | text | Label: "Market". |
| `modality` | text | Label: "Work modality". |
| `postings_seen_90d` | bigint | Label: "Postings (last 90 days)". |
| `pct_of_market_90d` | numeric | Label: "% of the market's postings (last 90 days)". |
| `postings_open_now` | bigint | Label: "Open postings now". |

### v_posting_counts

Headline counts per market and role. All sources; the *_full_text_* columns are the subset used by the tool views.

| Column | Type | Label / meaning |
|---|---|---|
| `market` | text | Label: "Market". CR, US-remote or LATAM-remote. |
| `role_category` | text | Label: "Role". |
| `postings_seen_90d` | bigint | Label: "Postings seen in the last 90 days" (includes postings that have since closed). |
| `postings_open_now` | bigint | Label: "Open postings now" (still listed as of the latest run). |
| `postings_full_text_seen_90d` | bigint | Label: "Postings with full description (last 90 days)". Base of the tool rankings. |
| `postings_full_text_open_now` | bigint | Label: "Open postings with full description". |
| `role_family` | text | Label: "Role family". |

### v_posting_skills

Long format: one row per (posting, tool). Postings with no tools have no rows here (use v_postings.n_tools). Filter description_is_full = TRUE for tool demand.

| Column | Type | Label / meaning |
|---|---|---|
| `job_id` | bigint | Posting key (joins v_postings). |
| `skill_name` | text | Label: "Tool". |
| `skill_category` | text | Label: "Tool category". |
| `role_family` | text | Label: "Role family". |
| `role_category` | text | Label: "Role". |
| `market` | text | Label: "Market". |
| `modality` | text | Label: "Work modality". |
| `source` | text | Label: "Source". |
| `description_is_full` | boolean | Label: "Full description". |
| `open_now` | boolean | Label: "Open now". |
| `seen_90d` | boolean | Label: "Seen in the last 90 days". |
| `first_seen` | timestamp with time zone | Label: "First seen by SkillMap". |
| `last_seen` | timestamp with time zone | Label: "Last seen by SkillMap". |

### v_postings

One row per loaded posting, for EDA. All sources, all time (filter seen_90d / open_now). The description text is deliberately left out: it can contain recruiter names and emails.

| Column | Type | Label / meaning |
|---|---|---|
| `job_id` | bigint | Posting key (joins v_posting_skills). |
| `source` | text | Label: "Source". greenhouse, lever, ashby, recruitee, personio (ATS boards), adzuna, jooble, remotive. |
| `company` | text | Label: "Company". |
| `title` | text | Label: "Job title". |
| `role_family` | text | Label: "Role family". |
| `role_category` | text | Label: "Role". |
| `market` | text | Label: "Market". CR, US-remote or LATAM-remote. |
| `modality` | text | Label: "Work modality". remote, hybrid, onsite or unspecified. |
| `location_raw` | text | Label: "Location (as posted)". Copies of one job in several cities are joined with " / ". |
| `salary_min` | numeric(12,2) | Label: "Salary min (annual)". Only salaries stated in the posting; hourly pay is not stored. |
| `salary_max` | numeric(12,2) | Label: "Salary max (annual)". |
| `salary_currency` | text | Label: "Currency". ISO code, e.g. USD. |
| `salary_mid` | numeric | Label: "Salary midpoint (annual)". Mean of min and max, or whichever exists. |
| `description_is_full` | boolean | Label: "Full description". FALSE for snippet sources (Adzuna, Jooble): not used for tool demand. |
| `posted_at` | timestamp with time zone | Label: "Posted (per source)". Can be the last update date on some ATSs. |
| `first_seen` | timestamp with time zone | Label: "First seen by SkillMap". |
| `last_seen` | timestamp with time zone | Label: "Last seen by SkillMap". |
| `open_now` | boolean | Label: "Open now". Still listed as of the latest run. |
| `seen_90d` | boolean | Label: "Seen in the last 90 days". The window the dashboard views use. |
| `days_listed` | integer | Label: "Days listed". Calendar days from first_seen to last_seen, inclusive. |
| `description_length` | integer | Label: "Description length (characters)". Snippets are ~500. |
| `n_tools` | bigint | Label: "Tools mentioned". |
| `url` | text | Label: "Posting link". Tracking parameters removed. |

### v_roles

Role categories and the family each belongs to. Use as a dimension / for sort order.

| Column | Type | Label / meaning |
|---|---|---|
| `role_family` | text | Label: "Role family". |
| `role_category` | text | Label: "Role". |
| `family_sort` | integer | Sort order for role_family. |
| `category_sort` | integer | Sort order for role_category. |

### v_skill_cooccurrence

Tools asked for in the same posting, directed: one row for (A,B) and one for (B,A). Full-description postings seen in the last 90 days. Filter skill_a to read "postings asking for A that also ask for B".

| Column | Type | Label / meaning |
|---|---|---|
| `skill_a` | text | Label: "Tool". |
| `skill_b` | text | Label: "Also asked for". |
| `postings_with_both_90d` | bigint | Label: "Postings asking for both (last 90 days)". |
| `postings_with_both_open_now` | bigint | Label: "Open postings asking for both". |
| `postings_with_a_90d` | bigint | Label: "Postings asking for the tool (last 90 days)". Denominator of pct_with_b_given_a_90d. |
| `pct_with_b_given_a_90d` | numeric | Label: "% that also ask for it (last 90 days)". postings_with_both_90d / postings_with_a_90d. |
| `n` | bigint | Label: "Sample size (postings)". Same as postings_with_a_90d. |
| `enough_data` | boolean | Label: "Enough data". TRUE when n >= 10. |

### v_skill_demand

Tool demand per role/market/modality. Full-description postings only (Adzuna/Jooble snippets excluded). Window: postings seen in the last 90 days; *_open_now columns restrict to postings still listed.

| Column | Type | Label / meaning |
|---|---|---|
| `role_category` | text | Label: "Role". |
| `market` | text | Label: "Market". CR, US-remote or LATAM-remote. |
| `modality` | text | Label: "Work modality". remote, hybrid, onsite or unspecified. |
| `skill_name` | text | Label: "Tool". |
| `skill_category` | text | Label: "Tool category". |
| `postings_mentioning_90d` | bigint | Label: "Postings mentioning the tool (last 90 days)". Full-description postings seen in the last 90 days that mention this tool. |
| `postings_total_90d` | bigint | Label: "Postings in segment (last 90 days)". Denominator for pct_postings_90d. |
| `pct_postings_90d` | numeric | Label: "% of postings asking for the tool (last 90 days)". postings_mentioning_90d / postings_total_90d. |
| `postings_mentioning_open_now` | bigint | Label: "Open postings mentioning the tool". Same, restricted to postings still listed (is_active). |
| `postings_total_open_now` | bigint | Label: "Open postings in segment". Denominator for an open-now percentage (can be 0). |
| `role_family` | text | Label: "Role family". Group of related role categories (see v_roles). |
| `n` | bigint | Label: "Sample size (postings)". Same as postings_total_90d: the denominator of pct_postings_90d. |
| `enough_data` | boolean | Label: "Enough data". TRUE when n >= 10. Show percentages only when TRUE. |

### v_skill_demand_segment

Tool demand per segment = (role category or role family) x market, all modalities. Full-description postings seen in the last 90 days. Filter grain to one value: rows of the two grains overlap.

| Column | Type | Label / meaning |
|---|---|---|
| `grain` | text | Which grouping the row belongs to: 'role_category' or 'role_family'. Always filter to one. |
| `segment` | text | Label: "Role / role family". The role category (grain = role_category) or role family (grain = role_family). |
| `role_family` | text | Label: "Role family". |
| `role_category` | text | Label: "Role". NULL at grain = role_family. |
| `market` | text | Label: "Market". |
| `skill_name` | text | Label: "Tool". |
| `skill_category` | text | Label: "Tool category". |
| `postings_mentioning_90d` | bigint | Label: "Postings mentioning the tool (last 90 days)". Additive across segments of one grain. |
| `postings_total_90d` | bigint | Label: "Postings in segment (last 90 days)". Denominator of pct_postings_90d. |
| `pct_postings_90d` | numeric | Label: "% of postings asking for the tool (last 90 days)". |
| `mention_share_pct` | numeric | Label: "Share of all tool requests (%)". This tool's mentions / all tool mentions in the segment. Sums to ~100 per segment; the skill-match % is the sum of this over the tools you have. |
| `demand_rank` | bigint | Label: "Rank in segment". 1 = most requested tool (ties share a rank). |
| `n` | bigint | Label: "Sample size (postings)". Same as postings_total_90d. |
| `enough_data` | boolean | Label: "Enough data". TRUE when n >= 10. |

### v_skills

Every tool in the dictionary, including ones no posting mentions yet. Use as the "My skills" slicer table for the skill-match page.

| Column | Type | Label / meaning |
|---|---|---|
| `skill_name` | text | Label: "Tool". |
| `skill_category` | text | Label: "Tool category" (BI, Database, Language, Cloud, ...). |
| `postings_mentioning_90d` | bigint | Label: "Postings mentioning the tool (last 90 days)". Full-description postings, all roles and markets. |

### v_weekly_postings

Postings per ISO week (Monday start, UTC), market and role. All sources. A posting is "listed" in every week between its first_seen and last_seen. Additive across markets and roles.

| Column | Type | Label / meaning |
|---|---|---|
| `week_start` | date | Label: "Week (starting Monday)". |
| `market` | text | Label: "Market". |
| `role_family` | text | Label: "Role family". |
| `role_category` | text | Label: "Role". |
| `new_postings` | bigint | Label: "New postings". First seen during the week. |
| `postings_listed` | bigint | Label: "Postings listed". Listed at any point during the week. |
| `postings_listed_full_text` | bigint | Label: "Postings listed (full description)". Subset used for tool trends. |

### v_weekly_skill_trend

Tool demand per ISO week, market and role family. Full-description postings listed during the week. postings_mentioning and n are additive across markets/families; percentages are not.

| Column | Type | Label / meaning |
|---|---|---|
| `week_start` | date | Label: "Week (starting Monday)". |
| `market` | text | Label: "Market". |
| `role_family` | text | Label: "Role family". |
| `skill_name` | text | Label: "Tool". |
| `skill_category` | text | Label: "Tool category". |
| `postings_mentioning` | bigint | Label: "Postings mentioning the tool". |
| `postings_listed_full_text` | bigint | Label: "Postings in segment". Denominator of pct_postings. |
| `pct_postings` | numeric | Label: "% of postings asking for the tool". |
| `rank_in_week` | bigint | Label: "Rank that week". 1 = most requested. |
| `n` | bigint | Label: "Sample size (postings)". Same as postings_listed_full_text. |
| `enough_data` | boolean | Label: "Enough data". TRUE when n >= 10. |

## Functions

`fn_skill_match(my_skills text[], match_grain text DEFAULT 'role_category'::text)` returns `TABLE(grain text, segment text, role_family text, market text, n bigint, enough_data boolean, match_pct numeric, skills_matched text[], top_missing_skills text[])`.

Skill match per segment and market. match_pct = share of tool requests in the segment's full-description postings (last 90 days) covered by my_skills (case-insensitive tool names). top_missing_skills = the 5 most-requested tools you do not have. match_grain: role_category (default) or role_family.

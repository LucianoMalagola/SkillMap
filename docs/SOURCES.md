# Source compliance register

Every data source considered for SkillMap, with what was checked and the decision.
Rule (see CLAUDE.md, "Source compliance"): only sources with an official/documented public
API or feed meant for this use, or whose terms explicitly allow automated access. Terms
**and** robots.txt are checked before any extractor is written. Never bypass logins,
CAPTCHAs or rate limits.

**Which rules govern what** (decided 2026-11-02): calls to **documented API endpoints** follow
that API's documentation and terms (rate limits, quotas, attribution, usage restrictions).
robots.txt governs **crawling web pages**; a robots.txt that disallows an API host does not
override the API's documented terms. A `Crawl-delay` on a host we call is honoured anyway.

Columns: **Decision**: allowed / rejected. Dates are when the terms/robots.txt were checked.

## In use

| Source | Access used | Terms / docs | robots.txt (host we call) | Decision | Reason | Checked |
|---|---|---|---|---|---|---|
| Greenhouse Job Board API | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs` | [docs](https://docs.greenhouse.io/job-board.html): "Job Board data is publicly available, so authentication is not required for any GET endpoints." | `boards-api.greenhouse.io`: only `/embed/` disallowed | **allowed** | Official, documented, public; we only call `/v1/boards/` | 2026-11-02 |
| Lever Postings API | `GET api.lever.co/v0/postings/{token}` | [docs](https://github.com/lever/postings-api): published postings are publicly viewable; API "designed to help you create a job site" | `api.lever.co`: `Allow: /`, **`Crawl-delay: 1`** | **allowed** | Official, documented, public. Crawl-delay respected: `extract_ats.py` waits 1 s between boards; `discover_boards.py` limits each host to 1 request/s (fixed 2026-11-02; it previously ran 4 workers in parallel) | 2026-11-02 |
| Ashby Job Posting API | `GET api.ashbyhq.com/posting-api/job-board/{token}` | [docs](https://developers.ashbyhq.com/docs/public-job-posting-api): data for "all currently published Job Postings… use this data to populate [a careers page]"; works without a key | `api.ashbyhq.com`: no robots.txt (401). (`jobs.ashbyhq.com` disallows `/api/`, a different host we don't call) | **allowed** | Official, documented, public | 2026-11-02 |
| Adzuna API | `GET api.adzuna.com/v1/api/jobs/us/search/{page}` with app_id/app_key | [terms](https://developer.adzuna.com/docs/terms_of_service): personal/academic research allowed; credit "The Adzuna API" with a link; limits may vary | `api.adzuna.com`: `User-agent: *` / `Disallow: /` | **allowed** (decided 2026-11-02) | We call only the documented, key-based search endpoint that Adzuna issues keys for, under its API terms: personal/academic research use, credit "The Adzuna API" with a link (README Credits), stay within the issued quota (~6 calls/run of ~1,000/month; `--max-calls` cap). The robots.txt disallow addresses crawlers of the host; we don't crawl pages. The app_id Adzuna echoes in responses is redacted before saving | 2026-11-02 |
| Jooble API | `POST jooble.org/api/{key}` | [API page](https://jooble.org/api/about): official API with a key | `jooble.org`: no rule for `/api/` (disallows `/desc/`, `/away/`, `/employer/api`… which we don't use) | **allowed** | Official, documented, key-based. We never fetch Jooble description pages (`/desc/`) | 2026-11-02 |
| Remotive API | `GET remotive.com/api/remote-jobs?category=…` | [docs](https://github.com/remotive-com/remote-jobs-api) + API "legal notice": max ~4 requests/day; link back and name Remotive; don't resubmit to job boards | `remotive.com`: `Disallow: /api/*` and `Disallow: /` | **allowed** (decided 2026-11-02) | We call only the documented public endpoint, which Remotive's own API notice invites developers to use, under its stated terms: max ~4 requests/day and never >2/min (we make 2 per run, 35 s apart), link back to the Remotive job URL and name Remotive as source (jobs.url + README Credits), no resubmission to job boards, no signup gating. robots.txt addresses crawlers of the site's pages; we don't crawl them. Note: currently returns a fixed 12-job sample | 2026-11-02 |

## Evaluated for Costa Rica coverage (ATS platforms)

| Source | Access considered | Terms / docs | robots.txt | Decision | Reason | Checked |
|---|---|---|---|---|---|---|
| Recruitee (Tellent) Careers Site API | `GET {company}.recruitee.com/api/offers/` | [docs](https://docs.recruitee.com/reference/offers): documented, no authentication; purpose "to display a list of job offers" | customer subdomain (`primeworks.recruitee.com`): only `/v/` disallowed | **allowed**. Rate-limited per platform domain (all `*.recruitee.com` / `*.jobs.personio.de` count as one host): Personio returned 429 once when limited per subdomain (fixed 2026-11-02) | Official, documented, public; structured `remote`/`hybrid`/`on_site` and `country_code` | 2026-11-02 |
| Personio XML feed | `GET {company}.jobs.personio.de/xml` | [docs](https://developer.personio.de/docs/retrieving-open-job-positions): "Current open job postings can be retrieved in XML format under myaccount.jobs.personio.de/xml" | company subdomains: `User-agent: *` / `Allow: /` | **allowed** | Official, documented feed of open postings | 2026-11-02 |
| SmartRecruiters Posting API | `api.smartrecruiters.com/v1/companies/{id}/postings` | [docs](https://developers.smartrecruiters.com/docs/posting-api): "supports both API Key and OAuth 2.0 Client Credentials authentication"; aimed at the customer's own career site | **`api.smartrecruiters.com`: only `LinkedInBot` allowed `/v1/companies/`; `User-agent: *` / `Disallow: /`** | **rejected** | robots.txt disallows all other agents on the API host; docs describe authenticated access (API key / OAuth, `internal_postings_read` scope) for customers' own career sites. Re-checked: unchanged | 2026-11-02 |
| Workable | `apply.workable.com/api/v1/widget/accounts/{slug}` | Official API needs a bearer token and targets HR integrations; the keyless "widget" endpoint is described only by third parties (e.g. [dev.to](https://dev.to/zsevic/integration-with-workable-public-jobs-api-3nk4)), not in Workable's docs | `apply.workable.com`: allows all (`Content-Signal: search=yes, ai-input=yes, ai-train=no`) | **rejected** | No official documentation of a public feed meant for this use; robots.txt alone isn't enough under the rule. Re-checked: still only third-party write-ups and scraper listings describe the keyless endpoints | 2026-11-02 |
| Breezy HR | `{company}.breezy.hr/json` | [API docs](https://developer.breezy.hr/docs) cover only the token-based `api.breezy.hr/v3`; the public `/json` feed appears only in third-party scraper listings | company subdomains: only CSS/JS/fonts disallowed | **rejected** | Feed not officially documented | 2026-11-02 |
| BambooHR | `{company}.bamboohr.com/careers/list` | Official API is HRIS-shaped and needs the customer's key; the careers JSON is undocumented (third-party sources say it changes without notice) | `www.bamboohr.com`: no relevant rule | **rejected** | Undocumented endpoint | 2026-11-02 |
| Workday (`myworkdayjobs.com`) | career sites | Workday's Terms of Use prohibit data mining, robots and scraping of its Sites; no official public jobs API | `www.myworkdayjobs.com`: no robots.txt reachable (tenant hosts only) | **rejected** for automation | Terms prohibit it (decision recorded 2026-11-02) | 2026-11-02 |

## Evaluated for Costa Rica coverage (job-data APIs)

| Source | Access considered | Terms / docs | robots.txt (host we call) | Decision | Reason | Checked |
|---|---|---|---|---|---|---|
| TheirStack Jobs API | `POST api.theirstack.com/v1/jobs/search` (bearer key), filters `job_country_code_or`, `job_title_pattern_or`, `posted_at_max_age_days`, `property_exists_or` | [Terms](https://theirstack.com/en/docs/legal/terms-and-conditions) §4.3: "Reposting, redistributing, and publicly displaying job postings, including on openly accessible [websites]" is permitted (the public-display ban covers only technographic / buying-intent data, which we don't use); §4.10: aggregated statistics, indexes and graphs the user generates are the user's own, provided they don't allow reconstructing a Dataset; §4.5: downloaded materials keep identifying info that they originated with TheirStack (no explicit public-attribution clause; we credit it anyway). TheirStack says it never logs in or bypasses CAPTCHAs ([data collection](https://theirstack.com/en/docs/legal/data-collection)). **Free plan** ([plans](https://theirstack.com/en/docs/pricing/plans)): 200 API credits/month, 1 credit per job returned, max 5 pages × 25 results per search; 4 req/s, 10/min, 50/h, 400/day; credits expire after a month on the free plan. A count (`limit: 1` + `include_total_results`) costs 1 credit if anything matches; the old free preview mode (`blur_company_data`) is deprecated. `GET /v0/catalog/jobs_companies_per_job_country_code` and `GET /v0/billing/credit-balance` are free | `api.theirstack.com`: `User-agent: *` / `Allow: /` | **allowed by its terms; build pending Luciano's OK** | Documented, key-based API whose terms allow public display of postings and aggregates. **Caveat:** in a 25-posting CR sample, 17 came from LinkedIn and 5 from Indeed (as collected by TheirStack), sources we reject for direct scraping. Option under review: request only postings with a `final_url` (employer career site / ATS page) | 2026-11-02 |
| JSearch (OpenWeb Ninja) | `GET api.openwebninja.com/jsearch/search` (`x-api-key`), `country=cr` | [Terms](https://www.openwebninja.com/terms) §2: license to "use, reproduce, and commercially exploit such API Data in your own products", but not as a standalone data product replicating the service; **"You are solely responsible for ensuring that your use… of API Data complies with… any terms that apply to the original source of such data."** Data comes from "LinkedIn, Indeed, Glassdoor, ZipRecruiter, and all public job sites via Google for Jobs" ([product page](https://www.openwebninja.com/api/jsearch)). Free Basic plan: 200 requests/month, hard limit ([RapidAPI listing](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch/details); OpenWeb Ninja's comparison page says 100), 1 request = 1 page of up to 10 jobs | `api.openwebninja.com`: no robots.txt (403 "Missing Authentication Token") | **pending: not tested** | Its own terms allow our use, but pass the original sites' terms (LinkedIn, Indeed, Google) on to us. The key returned 403 "You are not subscribed to this API" on both the OpenWeb Ninja and RapidAPI endpoints (2026-11-02), so no data was retrieved | 2026-11-02 |

## Rejected: do not use

Decided by Luciano 2026-11-02. These resell data scraped from sites whose terms prohibit scraping
(LinkedIn, Indeed, Glassdoor, ZipRecruiter, Monster), so they fail the compliance rule whatever
their own terms say.

| Source | What it is | Decision | Checked |
|---|---|---|---|
| OpenWeb Ninja platform-specific APIs: Real-Time LinkedIn Data, Real-Time Indeed Data, Real-Time Glassdoor Data, Real-Time ZipRecruiter Data, Real-Time Monster Data | Unofficial APIs over a single job platform | **rejected** | 2026-11-02 |
| Apify Indeed scrapers | Community actors that scrape Indeed pages | **rejected** | 2026-11-02 |
| Coresignal | Commercial datasets/APIs of jobs and profiles collected from LinkedIn and other platforms | **rejected** | 2026-11-02 |

## Job boards and other sites

| Source | Terms / docs | robots.txt | Decision | Reason | Checked |
|---|---|---|---|---|---|
| LinkedIn | [User Agreement](https://www.linkedin.com/legal/user-agreement) prohibits scraping | Header: "The use of robots or other automated means to access LinkedIn without the express permission of LinkedIn is strictly prohibited." | **rejected** | No public data API; scraping violates terms | 2026-11-02 |
| Indeed | Terms prohibit scraping; no public job-data API | Allows some paths for search engines only | **rejected** | No public data API; scraping violates terms | 2026-11-02 |
| Talento CR / PROCOMER | Postings behind a login with accepted T&C | n/a | **rejected** | Login-gated. Planned instead: contact PROCOMER to request data access (not now) | 2026-11-02 |
| Computrabajo (`cr.computrabajo.com`) | No official API found; terms page not found by search | **403 Forbidden** even for robots.txt (our user agent is blocked) | **rejected** | No API; site blocks automated clients | 2026-11-02 |
| elempleo (`elempleo.com/cr`) | [Términos](https://www.elempleo.com/cr/terminos-condiciones): use "únicamente para su uso personal y no comercial"; "Prohibida su reproducción total o parcial"; no API or feed mentioned | `User-agent: *` disallows admin/system paths only | **rejected** | No API; terms don't allow automated or reproduced use | 2026-11-02 |
| Buscojobs (`buscojobs.cr`) | No official API found | **Blocks `ClaudeBot` entirely**; disallows `/ofertas` and `/jobs` for several bots | **rejected** | No API; robots.txt excludes this agent | 2026-11-02 |
| TopTrabajos (`toptrabajos.com`) | No official API found | Disallows `/api/` and `/ofertas-de-trabajo/` | **rejected** | No API; job listings disallowed in robots.txt | 2026-11-02 |
| Encuentra24 (`encuentra24.com`) | No official API found; terms not found by search | Disallows `/api.php/`, `/ajax/`, `/captcha/`… | **rejected** | No API; terms don't explicitly allow automated access | 2026-11-02 |

## Company-name discovery (not posting extraction)

Reading public directories and news (CINDE, PROCOMER job-fair coverage, company press releases)
to learn **which companies operate in Costa Rica** is fine. Postings are then fetched only
through an allowed source above.

## Personal data

Postings can contain recruiter names and emails. Raw files stay out of git (and in short-lived,
credential-free artifacts). Dashboards show **aggregates only**; raw descriptions are never
published.

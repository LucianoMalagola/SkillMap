# Source compliance register

Every data source considered for SkillMap, with what was checked and the decision.
Rule (see CLAUDE.md, "Source compliance"): only sources with an official/documented public
API or feed meant for this use, or whose terms explicitly allow automated access. Terms
**and** robots.txt are checked before any extractor is written. Never bypass logins,
CAPTCHAs, rate limits or robots.txt.

Columns: **Decision**: allowed / rejected / ⚠ open (needs a decision). Dates are when the
terms/robots.txt were checked.

## In use

| Source | Access used | Terms / docs | robots.txt (host we call) | Decision | Reason | Checked |
|---|---|---|---|---|---|---|
| Greenhouse Job Board API | `GET boards-api.greenhouse.io/v1/boards/{token}/jobs` | [docs](https://docs.greenhouse.io/job-board.html): "Job Board data is publicly available, so authentication is not required for any GET endpoints." | `boards-api.greenhouse.io`: only `/embed/` disallowed | **allowed** | Official, documented, public; we only call `/v1/boards/` | 2026-11-02 |
| Lever Postings API | `GET api.lever.co/v0/postings/{token}` | [docs](https://github.com/lever/postings-api): published postings are publicly viewable; API "designed to help you create a job site" | `api.lever.co`: `Allow: /`, **`Crawl-delay: 1`** | **allowed** | Official, documented, public. Crawl-delay respected: `extract_ats.py` waits 1 s between boards; `discover_boards.py` limits each host to 1 request/s (fixed 2026-11-02; it previously ran 4 workers in parallel) | 2026-11-02 |
| Ashby Job Posting API | `GET api.ashbyhq.com/posting-api/job-board/{token}` | [docs](https://developers.ashbyhq.com/docs/public-job-posting-api): data for "all currently published Job Postings… use this data to populate [a careers page]"; works without a key | `api.ashbyhq.com`: no robots.txt (401). (`jobs.ashbyhq.com` disallows `/api/`, a different host we don't call) | **allowed** | Official, documented, public | 2026-11-02 |
| Adzuna API | `GET api.adzuna.com/v1/api/jobs/us/search/{page}` with app_id/app_key | [terms](https://developer.adzuna.com/docs/terms_of_service): personal/academic research allowed; credit "The Adzuna API" with a link; limits may vary | **`api.adzuna.com`: `User-agent: *` / `Disallow: /`** | **⚠ open**: allowed by terms (official, key-based API), but robots.txt disallows everything on the API host | Official API licensed by terms. Its robots.txt targets crawlers, but the rule says never bypass robots.txt, so this needs an explicit decision | 2026-11-02 |
| Jooble API | `POST jooble.org/api/{key}` | [API page](https://jooble.org/api/about): official API with a key | `jooble.org`: no rule for `/api/` (disallows `/desc/`, `/away/`, `/employer/api`… which we don't use) | **allowed** | Official, documented, key-based. We never fetch Jooble description pages (`/desc/`) | 2026-11-02 |
| Remotive API | `GET remotive.com/api/remote-jobs?category=…` | [docs](https://github.com/remotive-com/remote-jobs-api) + API "legal notice": max ~4 requests/day; link back and name Remotive; don't resubmit to job boards | **`remotive.com`: `Disallow: /api/*` and `Disallow: /`** | **⚠ open**: documented and invited by its own legal notice, but robots.txt disallows `/api/*`. Also currently returns a fixed 12-job sample | Same conflict as Adzuna; low value right now | 2026-11-02 |

## Evaluated for Costa Rica coverage (ATS platforms)

| Source | Access considered | Terms / docs | robots.txt | Decision | Reason | Checked |
|---|---|---|---|---|---|---|
| Recruitee (Tellent) Careers Site API | `GET {company}.recruitee.com/api/offers/` | [docs](https://docs.recruitee.com/reference/offers): documented, no authentication; purpose "to display a list of job offers" | customer subdomain (`primeworks.recruitee.com`): only `/v/` disallowed | **allowed**. Rate-limited per platform domain (all `*.recruitee.com` / `*.jobs.personio.de` count as one host): Personio returned 429 once when limited per subdomain (fixed 2026-11-02) | Official, documented, public; structured `remote`/`hybrid`/`on_site` and `country_code` | 2026-11-02 |
| Personio XML feed | `GET {company}.jobs.personio.de/xml` | [docs](https://developer.personio.de/docs/retrieving-open-job-positions): "Current open job postings can be retrieved in XML format under myaccount.jobs.personio.de/xml" | company subdomains: `User-agent: *` / `Allow: /` | **allowed** | Official, documented feed of open postings | 2026-11-02 |
| SmartRecruiters Posting API | `api.smartrecruiters.com/v1/companies/{id}/postings` | [docs](https://developers.smartrecruiters.com/docs/posting-api): "supports both API Key and OAuth 2.0 Client Credentials authentication"; aimed at the customer's own career site | **`api.smartrecruiters.com`: only `LinkedInBot` allowed `/v1/companies/`; `User-agent: *` / `Disallow: /`** | **rejected** | robots.txt disallows all other agents on the API host; docs describe authenticated access | 2026-11-02 |
| Workable | `apply.workable.com/api/v1/widget/accounts/{slug}` | Official API needs a bearer token and targets HR integrations; the keyless "widget" endpoint is described only by third parties (e.g. [dev.to](https://dev.to/zsevic/integration-with-workable-public-jobs-api-3nk4)), not in Workable's docs | `apply.workable.com`: allows all (`Content-Signal: search=yes, ai-input=yes, ai-train=no`) | **rejected** | No official documentation of a public feed meant for this use; robots.txt alone isn't enough under the rule | 2026-11-02 |
| Breezy HR | `{company}.breezy.hr/json` | [API docs](https://developer.breezy.hr/docs) cover only the token-based `api.breezy.hr/v3`; the public `/json` feed appears only in third-party scraper listings | company subdomains: only CSS/JS/fonts disallowed | **rejected** | Feed not officially documented | 2026-11-02 |
| BambooHR | `{company}.bamboohr.com/careers/list` | Official API is HRIS-shaped and needs the customer's key; the careers JSON is undocumented (third-party sources say it changes without notice) | `www.bamboohr.com`: no relevant rule | **rejected** | Undocumented endpoint | 2026-11-02 |
| Workday (`myworkdayjobs.com`) | career sites | Workday's Terms of Use prohibit data mining, robots and scraping of its Sites; no official public jobs API | `www.myworkdayjobs.com`: no robots.txt reachable (tenant hosts only) | **rejected** for automation | Terms prohibit it (decision recorded 2026-11-02) | 2026-11-02 |

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

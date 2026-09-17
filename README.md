# jobscrap

Remote-first job aggregator filtered for early-career software roles. Pulls from
public job-board APIs on a schedule, normalizes everything into one schema,
and ranks by whether *you* can actually take the role.

The core idea: **don't scrape on click.** Scrape continuously in the background,
then serve instant indexed queries. A click is a 17ms DB read, not a 4-minute crawl.

## Layout

```
backend/
  app/
    core/         settings (pydantic-settings) + search profile (config.yaml)
    domain/       entities.py, enums.py - pure types, no I/O
    connectors/   one module per source, all behind Connector ABC
    services/
      normalization/  rules-first extraction (no LLM)
      enrichment/     Gemini provider, key pool, batched extractor
      dedupe/         url + composite-key collapse
      scoring/        transparent weighted match score
    db/           ORM, engine/session, repository (all SQL lives here)
    api/v1/       FastAPI read-only routes
    pipeline/     orchestrator + operator CLI
  scripts/        probe_gemini.py
frontend/         React + Vite + Tailwind
.github/workflows/scrape-jobs.yml    scheduled scraping (free on public repos)
```

## Setup

```bash
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt

# env is split per service - backend holds secrets, frontend holds public values
cp backend/.env.example  backend/.env      # DATABASE_URL, GEMINI_API_KEYS
cp frontend/.env.example frontend/.env     # VITE_API_BASE (leave empty in dev)

cd frontend && npm install
```

Only `backend/.env` ever holds secrets. Vite exposes every `VITE_*` variable to
the browser, so `frontend/.env` is public by construction - never put a key there.

## Run

```bash
# scrape jobs (from backend/) - GitHub Actions runs this for you on a schedule
python -m app.pipeline.cli scrape --tier 1
python -m app.pipeline.cli scrape --tier 1 --no-enrich   # skip the LLM pass
python -m app.pipeline.cli show --limit 20 --reachable
python -m app.pipeline.cli health
python -m app.pipeline.cli rescore                       # recompute scores after changing config.yaml, no refetch
python -m app.pipeline.cli export                        # publish frontend/public/jobs.json (the site loads this)
python -m pytest tests -q                                # rules, scoring and storage-lifecycle tests
python -m app.pipeline.cli reset --yes                   # empty the job table (manual only; closed jobs are removed automatically)
python -m app.pipeline.cli import-companies --platform workday --apply   # add employers with India openings
# also: --platform greenhouse | lever | ashby (public dataset) and freshteam | keka | gem | recruitee | workable | rippling (Common Crawl)

# api
uvicorn app.main:app --reload        # docs at /docs

# frontend
cd frontend && npm run dev           # proxies /api to :8000
```

Verify your Gemini setup — this asks the API which models your key can use,
rather than guessing:

```bash
cd backend && python -m scripts.probe_gemini
```

## Sources

**Tier 1 - employer ATS boards (the good data).** One connector per platform,
driven by `backend/companies.yaml` (~1,600 verified employers across all platforms). Structured
locations, real posting dates, direct apply links.

| Platform | Endpoint | Notes |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` | `location.name` like "Bengaluru, Karnataka, India"; use `first_published`, not `updated_at` (an edit bumps the latter) |
| Lever | `api.lever.co/v0/postings/{slug}?mode=json` | **Bare array**, title is `text` not `title`, `createdAt` in epoch **ms**, `country` + `workplaceType` structured |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{slug}` | `isRemote` boolean, `employmentType`, `secondaryLocations`. Tightest limiter - concurrency 5 |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{slug}/postings` | Best structured data: `location.country` ISO, `location.remote` bool, **`experienceLevel: entry_level`**. Title is `name`. Returns `200 + empty` for unknown slugs, never 404 |
| Workable (per company) | `apply.workable.com/api/v1/widget/accounts/{slug}?details=true` | Whole board incl. descriptions; `locations[].countryCode`, `telecommuting` |
| Freshteam | `{slug}.freshteam.com/hire/widgets/jobs.json` | Popular with Indian startups. Location lives on `branches[]` (`country_code` IN) |
| Keka | `{slug}.keka.com/careers/api/embedjobs/default/active/{portal_id}` | Indian startups. Portal id is read from the careers page first; `experience` ("1-3 Years") feeds the YoE gate |
| Recruitee | `{slug}.recruitee.com/api/offers/` | `employment_type_code`, `experience_code: entry_level` |
| Gem | `api.gem.com/job_board/v0/{slug}/job_posts/` | Greenhouse-shaped; `location_type` remote/hybrid/onsite |
| Rippling | `ats.rippling.com/api/v2/board/{slug}/jobs` | Paged; structured `countryCode` + `workplaceType` |

**Tier 1 - enterprise career sites.** Large employers' India internships
almost never reach Greenhouse or Lever; they live on the employer's own
system, which has to be *searched* per term rather than read whole. Terms and
locations come from `config.yaml → search`.

| Source | How it's read | Notes |
|---|---|---|
| Workday | `POST {tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` | Nvidia, Adobe, Salesforce, Intel and India-hiring tenants imported from a public dataset. `limit` caps at 20 — ask for more and you get an **empty** 200. Search is fuzzy: "India" also matches Indiana |
| Google Careers | results page, `AF_initDataCallback` `ds:1` block | No JSON API; jobs are embedded JSON, parsed with a real decoder. Structured country codes and eligibility notes |
| Amazon Jobs | `amazon.jobs/en/search.json` | `normalized_country_code[]=IND` works; `country[]` is silently ignored. Some campus roles are link-only (`isUnsearchable`) and can't be listed by anyone |
| Avature (EA) | `{base}/SearchJobs/{term}` HTML | Structured spans for location, role id, worker type. The RSS feed ignores the search term |
| Juspay | `juspay.io/careers` | Astro site; jobs are serialized island props |
| Oracle HCM | `{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions` | JPMorgan Chase, Oracle, Texas Instruments. Public finder query with keyword + location |
| Microsoft | `apply.careers.microsoft.com/api/pcsx/search` | Eightfold PCSX; 10 per page, structured `standardizedLocations` |
| Apple | `jobs.apple.com/en-in/search` HTML | Data embedded as `__staticRouterHydrationData`; the JSON API needs CSRF and returns nothing |
| Atlassian | `atlassian.com/endpoint/careers/listings` | Every open role in one array, full descriptions |
| Goldman Sachs | `api-higher.gs.com/gateway/api/v1/graphql` | India filter; Associate/VP/MD ranks dropped at the source |
| IBM | `www-api.ibm.com/search/api/v2` | Elasticsearch-style; country in `field_keyword_05`, type in `field_keyword_18` |
| Eightfold | `{host}/api/apply/v2/jobs` | Netflix; add more under `eightfold:` in companies.yaml |
| SuccessFactors | `{base}/search/?q=&locationsearch=` HTML | SAP; generic for SAP-hosted career sites (`successfactors:` in companies.yaml) |

**Tier 1 - VC portfolio job boards.** Every employer on these is venture-funded,
and links go to the company's own ATS posting. Hosts live in companies.yaml.

| Platform | How it's read | Boards |
|---|---|---|
| Consider | `GET {host}/jobs` for the CSRF token + board id, then `POST {host}/api-boards/search-jobs` | Peak XV, Sequoia, Lightspeed, Bessemer, GV, Kleiner Perkins, First Round, Battery, Felicis, CRV, Initialized, USV, Costanoa |
| Getro | network id from `__NEXT_DATA__`, then `POST api.getro.com/api/v2/collections/{id}/search/jobs` (needs `Accept: application/json`) | Accel, Blume, 3one4, Antler, General Catalyst, Insight, Khosla, Thrive, 8VC, Menlo, DCVC, Techstars and more |

**Tier 1 - screened Indian tech hiring:** Instahyre's public job search
(`/api/v1/job_search?years=0`), fresher-eligible and internship roles only.
Mass-internship marketplaces (Unstop, Internshala) and LinkedIn are
deliberately not used: too many unpaid or unverifiable listings.

| Indian ATS | Endpoint | Notes |
|---|---|---|
| Zoho Recruit | `{company}.zohorecruit.in/jobs/Careers` | Jobs are embedded as JSON in `<input id="jobs">`; `Work_Experience`, `Job_Type`, `Date_Opened` |

**Tier 1 - curated feeds:** [SimplifyJobs](https://github.com/SimplifyJobs)
Summer 2027 internships and new-grad lists — thousands of active roles with a
maintained `active` flag. US-heavy: it adds global and remote reach, not India.

**Tier 1 - remote-native aggregators:** Himalayas (honest `locationRestrictions`),
RemoteOK (attribution required).

**Tier 3 - low signal, kept for coverage:** HN "Who is Hiring" (monthly thread,
so posts are days old on arrival), Arbeitnow (all professions, Germany-heavy),
Remotive (public API now returns ~16 jobs total - effectively dead).

### Growing coverage

```bash
python -m app.pipeline.cli discover --platform greenhouse --pages 4 --apply
python -m app.pipeline.cli validate --apply
```

`discover` reads the free Common Crawl CDX index for ATS URL patterns and
extracts slugs. `validate` calls every candidate board and promotes the ones
that answer with postings. A slug is pruned **only** on a definitive 404, and
pruned slugs are remembered under `rejected:` so later runs don't re-test them.

`import-companies` starts from the company lists published by
[Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator)
(MIT) — ~12,900 Workday tenants and ~8,300 Greenhouse boards — and probes each
**once** for real India openings. Only employers that have them are added;
those with India internships go to tier 1. Scraping all of them daily would
exceed the free tier and add nothing for an India-based search.

### Startups' own careers pages

```bash
python -m app.pipeline.cli detect-careers --source yc --apply   # ~1,600 YC companies
python -m app.pipeline.cli detect-careers --source vc --apply   # portfolio companies on the VC boards
```

Guessing a startup's board token from its name misses most of them, so this
reads the company's website instead: the homepage, then up to three
careers/jobs/join-us pages, pulling out links to any supported hiring system
(Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee, Freshteam,
Keka, Zoho Recruit, Rippling, Gem). Each board is probed with the real
connector and kept only if it has India or worldwide openings, then saved to
companies.yaml with the company's `website`. A site that links to many boards
is a recruiting product showing customers, so those boards are named after
themselves. The workflow re-runs both scans on the 1st of every month.

YC startups that post only on ycombinator.com are read directly by the
`yc_jobs` connector (1,500 hiring companies, fetched live each run).

### Eligibility gates

A posting you cannot apply to is worse than none: it costs attention and
pushes a real match down the list. Beyond role, experience and region, a job
is dropped when it

* **demands a PhD or Master's** with no bachelor's path ("Software Engineering
  PhD Intern"; "BS/MS" is fine, and so is "Bachelor's or Master's"),
* **requires US eligibility** ("enrolled at a US university", "US citizens
  only", "authorized to work in the United States") and isn't an India role,
* **names eligible batches that exclude yours** ("2025 & 2026 batch only",
  read from `profile.graduating` in config.yaml), or
* **states that it is unpaid.**

Stated pay is extracted while the description is in memory - monthly stipends
(`₹25,000/month`), annual CTC (`12 LPA`, `₹8-12 LPA`) and dollar figures - and
kept as a label on the card, only when it appears near a pay word so "25,000
users" can't be mistaken for a stipend. Each gate is switchable under
`experience:` in config.yaml, and all of them are covered by tests.

### The site loads a static snapshot

Every scrape writes `frontend/public/jobs.json` (~100 kB gzipped for ~1,300
jobs) and commits it, so Vercel serves the list from its CDN: the page renders
immediately, filters and sorts in the browser, and works while Render's free
tier is asleep - which used to mean waiting up to a minute for the first
visit. The API stays available and is the automatic fallback when the snapshot
is missing (for example in `npm run dev` before the first export).

Saved jobs, applied jobs and "new since your last visit" live in the
browser's localStorage: no login, no server, nothing to pay for.

### Data lifecycle (built for the free tier)

The database holds **only what the site shows**: live postings that pass every
filter. At ~0.5 KB a row, a few thousand jobs fit in 5-15 MB of Neon's 0.5 GB.

* **No descriptions stored.** They are read during the scrape, in memory, to
  extract skills, experience and region (rules first, Gemini for what rules
  can't resolve), then discarded. They used to be ~60% of the table.
* **Only matching jobs stored.** A posting that fails a filter (role, experience,
  region, age) is never written. Previously ~20k rejected rows sat in the table.
* **Unchanged jobs aren't rewritten.** Each row carries a fingerprint of its
  values; a run inserts new jobs, updates changed ones and skips the rest -
  fewer writes, fewer dead rows, less database compute.
* **Closed jobs are deleted as soon as a run proves it.** Every job has a
  *scope* (`greenhouse:stripe`, `workday:nvidia:External`, `yc_jobs:posthog`).
  A job missing from a run is deleted only if its scope was fetched
  successfully in that run - a timeout at one company never touches its jobs,
  and the 6-hourly run never judges tier-2 companies it didn't check. Sources
  that return complete listings delete on the first miss; search-style sources
  (Workday, Google, aggregators) on the second consecutive miss, since a search
  can omit a live job once. Dead apply links are deleted too.
* **No daily wipe.** The 00:00 IST run is simply the full scrape.

Side tables are bounded: the LLM cache keeps 60 days (keyed by a description
hash, so a posting seen daily costs one Gemini call), run-health history 14
days. On first start the new code migrates an old database automatically:
drops the description column, deletes filtered-out rows and compacts the
table (`VACUUM FULL`).

### Tests

```bash
cd backend && python -m pytest tests -q
```

No network and no real database - SQLite in a temp dir. They cover the parts
where a quiet mistake hides good jobs: role classification, the experience and
eligibility gates, pay parsing, scoring order, and the storage lifecycle
(insert / skip-unchanged / delete-when-closed / never-delete-on-a-failed-run).
GitHub runs them on every push that touches `backend/`.

### The `[]` trap

Every one of these APIs collapses three outcomes into an empty array: board
doesn't exist, board exists with zero openings, request failed. Treating them
alike makes a broken connector look identical to a company that stopped
hiring, so coverage rots silently. `BoardStatus` keeps them apart
(`OK` / `EMPTY_BOARD` / `NO_BOARD` / `FETCH_FAILED`) and only `NO_BOARD`
prunes a slug.

Adding a platform: subclass `ATSConnector`, implement `board_url()` and
`parse()`, register in `app/connectors/ats/__init__.py`. Set
`impersonate = "chrome124"` if the endpoint 403s a plain client - that's TLS
fingerprinting, not auth, and `curl_cffi` handles it.


## Deploying

Three pieces, each free, each with a different lifetime. The split matters:
**scraping cannot live on Render** because every free host suspends an idle
service, and a suspended container runs no cron.

```
GitHub Actions   scrape + enrich + discovery   (cron, always fires)
      │  writes
      ▼
Neon Postgres    the corpus                    (persistent)
      ▲  reads
      │
Render           FastAPI, read-only            (sleeps when idle - fine)
      ▲
Vercel           React SPA                     (static, always on)
```

### 1. Neon Postgres

Create a project at [neon.tech](https://neon.tech), then copy the **pooled**
connection string and **change the driver prefix**:

```
Neon gives you:  postgresql://user:pass@ep-xxx-pooler.../db?sslmode=require
you must use:    postgresql+psycopg://user:pass@ep-xxx-pooler.../db?sslmode=require
                           ^^^^^^^^
```
Without `+psycopg`, SQLAlchemy tries psycopg2 and the deploy fails at startup.

Tables are created automatically on first run - there is no migration step.

### 2. Render (backend)

Configured in the dashboard, no config file. **New → Web Service**, connect
this repo, then:

| Setting | Value |
|---|---|
| Branch | `main` |
| Region | **Ohio** (same AWS region as Neon us-east-2) |
| Root Directory | `backend` |
| Runtime | Python 3 |
| Build Command | `pip install -r requirements.txt` |
| Start Command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT` |
| Instance Type | Free |
| Health Check Path (Advanced) | `/health` |
| Auto-Deploy (Advanced) | On Commit |

Environment variables:

| Variable | Value |
|---|---|
| `PYTHON_VERSION` | `3.12.10` — Render's default for new services is 3.14 |
| `DATABASE_URL` | the `+psycopg` string from step 1 |
| `CORS_ORIGINS` | `https://<your-project>.vercel.app` (no trailing slash) |

`.python-version` is also committed at the repo root and in `backend/`, but
the env var takes precedence and removes any doubt about which file Render
reads when a Root Directory is set. `CORS_ORIGIN_REGEX` needs no value: the
code defaults it to `https://.*\.vercel\.app`, which covers preview deploys.

Because Root Directory is `backend`, frontend-only pushes don't redeploy the
API. The scraper bot's commits carry `[skip render]`, so coverage updates
don't either.

The free plan sleeps after ~15 minutes idle, so the first request after a
quiet spell takes up to a minute. Nothing is lost - the API is read-only.

### 3. Vercel (frontend)

**Add New → Project**, then:

- **Root Directory:** `frontend`
- **Environment Variable:** `VITE_API_BASE` = `https://<your-api>.onrender.com`
  — add it to **Production *and* Preview**

Vite inlines `VITE_*` at **build** time, so changing this needs a redeploy,
not just a restart. Push-to-deploy is on by default.

### 4. GitHub Actions (the scraper)

Repo → Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `DATABASE_URL` | same `+psycopg` string |
| `GEMINI_API_KEY_1` … `_3` | your keys |

Optional variable: `GEMINI_MODEL` (defaults to `gemini-3.5-flash-lite`).

Schedules: tier 1 every 6h, tier 2 (all 800+ boards) daily at 02:00 UTC.
Run **Actions → Scrape jobs → Run workflow** with **discover ✓** to refresh the YC
list and validate new boards.

> **Why the workflow commits back to the repo:** `companies.yaml` and
> `yc_index.json` *are* the product - 800+ verified boards. The Actions
> runner filesystem is discarded after every run, so discovery results must
> be committed or they vanish. That's what `permissions: contents: write`
> and the "Commit discovered boards" step are for. It also keeps the
> schedule alive, since Actions disables cron after 60 days of inactivity.

### Verify a deploy

```bash
curl https://<your-api>.onrender.com/health   # liveness, no DB touch
curl https://<your-api>.onrender.com/ready    # actually queries Postgres
```

### Env var reference

| Variable | Local | Render | Actions | Vercel |
|---|---|---|---|---|
| `DATABASE_URL` | ✅ sqlite | ✅ neon | ✅ neon | — |
| `GEMINI_API_KEY_1..3` | ✅ | — (API never calls Gemini) | ✅ | — |
| `GEMINI_MODEL` | ✅ | — | optional | — |
| `CORS_ORIGINS` | default | ✅ | — | — |
| `CORS_ORIGIN_REGEX` | default | preset | — | — |
| `VITE_API_BASE` | ✅ | — | — | ✅ |

Nothing secret ever reaches the frontend: every `VITE_*` value ships to the
browser, which is why the API keys live only where the scraper runs.

## Design decisions worth knowing

**`hiring_regions` is the point.** Most postings tagged "Remote" mean
remote-within-one-country. Conflating `worldwide` with `us` produces a board
full of roles that need US work authorization. Everything — schema, scoring,
UI colour — is organized around that distinction.

**`min_yoe = None` ≠ `min_yoe = 0`.** None means the posting states nothing,
which is the common case for genuine new-grad reqs. `include_unknown_yoe`
controls whether those are kept; setting it `false` today leaves almost nothing.

**Enrichment runs after storage, before filtering.** Filtering first would drop
postings whose region is merely *unresolved* rather than genuinely unreachable.

**`first_seen_at` over `posted_at`.** Many sources only expose `updated_at`, and
several report dates unreliably. Our own first-sighting is the honest signal.

**`last_seen_at` + `is_active`.** A posting that vanishes from its board for two
runs is marked inactive. Knowing a role is *still open* is the real edge over
LinkedIn, where a large share of listings are month-old ghosts.

**Extraction is cached by `description_hash`.** A posting reappearing in daily
runs for a month costs exactly one LLM call. This is what keeps the free tier viable.

**Freshness is source-aware.** Aggregators keep dead listings around, so a
2-day age cap is the only defence there. Employer ATS boards return *only*
open requisitions - a 30-day-old Greenhouse posting is still live. Capping
those at 2 days threw away 105 of 111 real Indian engineering roles.

**Rules own role classification; the LLM may only refine it.** The model was
relabelling out-of-scope roles into scope (`other` -> `devops`), silently
overriding config.yaml. LLM output is now clamped to the configured
categories, on fresh extraction and on cache read.

**Title beats metadata, and rank beats level.** "Data Scientist I" is entry
level even when SmartRecruiters tags it `mid_senior_level`; "Principal
Software Engineer 1" is not entry level despite the trailing 1.

**One pooled HTTP client per connector.** A fresh `AsyncClient` per request
meant 30 simultaneous TLS handshakes at concurrency 30, which surfaced as
`ConnectError` on endpoints that answer a pooled client fine.

**The LLM extracts, it never guesses.** `unknown` is an expected answer, and
low-confidence regions are discarded rather than stored — a confident wrong
region is worse than none, because the UI would present it as fact.

## Cost

$0. Neon Postgres free tier, GitHub Actions cron (unlimited minutes on public
repos), Vercel/Cloudflare Pages for the frontend, Gemini free tier for the
~4 calls/day the cache leaves behind.

The scheduler deliberately lives in Actions, not in the API container: every
free host sleeps an idle service, and a sleeping container runs no cron.

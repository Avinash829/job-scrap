# jobscrap

Remote-first job aggregator filtered for early-career software roles. Pulls from
public job-board APIs on a schedule, normalizes everything into one schema,
and ranks by whether *you* can actually take the role.

The core idea: **don't scrape on click.** Ingest continuously in the background,
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
.github/workflows/ingest.yml    scheduled ingest (free on public repos)
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
# ingest (from backend/)
python -m app.pipeline.cli ingest --tier 1
python -m app.pipeline.cli ingest --tier 1 --no-enrich   # skip the LLM pass
python -m app.pipeline.cli enrich                        # LLM pass only, no refetch
python -m app.pipeline.cli show --limit 20 --reachable
python -m app.pipeline.cli health
python -m app.pipeline.cli purge --days 60

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
driven by `backend/companies.yaml` (322 verified boards). Structured
locations, real posting dates, direct apply links.

| Platform | Endpoint | Notes |
|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true` | `location.name` like "Bengaluru, Karnataka, India"; use `first_published`, not `updated_at` (an edit bumps the latter) |
| Lever | `api.lever.co/v0/postings/{slug}?mode=json` | **Bare array**, title is `text` not `title`, `createdAt` in epoch **ms**, `country` + `workplaceType` structured |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{slug}` | `isRemote` boolean, `employmentType`, `secondaryLocations`. Tightest limiter - concurrency 5 |
| SmartRecruiters | `api.smartrecruiters.com/v1/companies/{slug}/postings` | Best structured data: `location.country` ISO, `location.remote` bool, **`experienceLevel: entry_level`**. Title is `name`. Returns `200 + empty` for unknown slugs, never 404 |

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
that answer with postings. A slug is pruned **only** on a definitive 404.

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
**ingestion cannot live on Render** because every free host suspends an idle
service, and a suspended container runs no cron.

```
GitHub Actions   ingest + enrich + discovery   (cron, always fires)
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
API. The ingest bot's commits carry `[skip render]`, so coverage updates
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

### 4. GitHub Actions (the ingest)

Repo → Settings → Secrets and variables → Actions:

| Secret | Value |
|---|---|
| `DATABASE_URL` | same `+psycopg` string |
| `GEMINI_API_KEY_1` … `_3` | your keys |

Optional variable: `GEMINI_MODEL` (defaults to `gemini-3.5-flash-lite`).

Schedules: tier 1 every 6h, tier 2 (all 800+ boards) daily at 02:00 UTC.
Run **Actions → ingest → Run workflow** with **discover ✓** to refresh the YC
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
browser, which is why the API keys live only where ingest runs.

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

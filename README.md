# career-copilot

A local FastAPI service that ingests job vacancies, scores them against my
own profile, and helps me decide where to apply — plus generates tailored
cover letters. Built openly as a case study in using AI agents to design and
implement a real service: see `/docs` (coming soon) for the accompanying
article series on methodology.

## Status

Early scaffold. Ingestion layer first: pulling vacancies from multiple
sources behind one interface before anything else gets built on top.

## Ingestion sources

- **Arbeitnow** — no API key needed. Confirmed live: the API's
  `search`/`tags` query params are accepted but ignored server-side, so
  filtering happens client-side after fetching. PHP/Symfony volume is low
  (roughly 1 relevant listing per 175), so this is a supplementary source.
  Rate-limits hard (hit a real 429 behind a Cloudflare challenge in
  testing) - `ArbeitnowSource` paces requests accordingly.
- **Adzuna** — needs a free `app_id`/`app_key` from
  https://developer.adzuna.com/. Real server-side filtering by keyword
  (`what`) and location (`where`), with `pl` as a supported country code for
  Poland. This is the primary source once keys are in place.
- **justjoin.it** — no partner API (their `/api/` is a private frontend
  backend, and `robots.txt` explicitly disallows it). Reads their published
  sitemap for URLs and the schema.org `JobPosting` JSON-LD each job page
  embeds for search engines. Slow (one full page fetch per match) - meant
  to run occasionally, not on every request.

Each source's actual fetch/parse logic now lives in its own standalone
repo, not here - `AdzunaSource`/`ArbeitnowSource`/`JustJoinItSource` in
`src/ingestion/sources/` are thin adapters mapping each library's own
`Job` model onto this project's `Vacancy` DTO:

- [`adzuna-client`](../adzuna-client)
- [`arbeitnow-client`](../arbeitnow-client)
- [`justjoinit-scraper`](../justjoinit-scraper)

Each is independently installable and usable without this project - see
their own READMEs for what was learned building them against the live
APIs (rate limits, salary formats, what's actually filterable
server-side). Expected to live as siblings of this repo (`Work/adzuna-client`,
etc.) - both local dev and the Docker build (context is the parent
directory, see Dockerfile) assume that layout.

Nothing above is fetched live on a request anymore. `scripts/ingest.py
--source <name>` pulls from one source and upserts into the DB - each
source is its own command deliberately, since they have very different
cost/speed profiles and shouldn't share a schedule:

```bash
.venv/bin/python scripts/ingest.py --source adzuna
.venv/bin/python scripts/ingest.py --source arbeitnow
.venv/bin/python scripts/ingest.py --source justjoinit  # slow, run less often
```

Works the same way inside the container: `docker compose exec api python
scripts/ingest.py --source adzuna`. Wire these into cron at whatever
per-source cadence makes sense - see the script's docstring for an example.
`GET /vacancies` and `GET /recommendations` read whatever's in the DB; if
they come back empty, nothing has been ingested yet.

Each successful ingest also publishes newly-inserted vacancy ids to a
RabbitMQ queue (`vacancy.embed`) - see Matching below for what consumes it.
Only genuinely new vacancies get queued, not ones just refreshed on
re-ingest (re-embedding unchanged text would be wasted work). If vacancies
exist in the DB but were never queued for some reason (e.g. ingested before
this pipeline existed), `scripts/backfill_embeddings.py` queues everything
currently stored - idempotent, safe to run anytime.

## Matching

Two-stage pipeline. First stage is semantic, not keyword-based: the
profile is embedded (`query: ...` prefix) and matched via Elasticsearch
kNN against vacancy embeddings, computed by a separate **embedding-worker**
service that consumes `vacancy.embed`, fetches the vacancy text from
MySQL, embeds it (`passage: {title}\n\n{description}`) via the TEI service
in `career-copilot-infra`, and indexes the vector. This replaced an
earlier keyword-overlap heuristic entirely - free to run, but blind to
synonyms and much cheaper to scale to a large vacancy pool than scoring
every vacancy with an LLM call.

`llm_rerank_top_n=N` (opt-in, 0 by default) is the second stage: sends
only the top N of the vector-search shortlist to an LLM for semantic
reasoning — seniority fit, overqualification risk, gaps a similarity
score alone can't explain.

The LLM call goes through the local `claude` CLI (`claude -p ...`), not the
Anthropic API — it reuses your already-authenticated Claude Code session, no
API key to manage. That means the container needs your `~/.claude` session
mounted in (see docker-compose.yml): it's mounted read-only, but it does give
the container the ability to *use* your real Claude Code session while
running. If that's not something you want to grant a container, run
`llm_rerank_top_n` locally via `.venv` instead — the Docker path works
without it, just without the LLM rerank layer.

## Setup

### Docker (recommended)

Requires [`career-copilot-infra`](../career-copilot-infra) running first
(shared embedding service + the `career-copilot-external` network both
projects attach to):

```bash
cd ../career-copilot-infra && docker compose up -d
cd ../career-copilot
cp .env.example .env  # fill in ADZUNA_APP_ID / ADZUNA_APP_KEY
docker compose up --build -d
docker compose exec api python scripts/ingest.py --source adzuna
curl http://localhost:8000/health
curl "http://localhost:8000/vacancies?keywords=php,symfony"
curl "http://localhost:8000/recommendations?top_k=10&llm_rerank_top_n=5"
```

### Local (no Docker)

```bash
uv venv
uv pip install -r requirements.txt
uv pip install -e ../adzuna-client -e ../arbeitnow-client -e ../justjoinit-scraper
cp .env.example .env  # fill in ADZUNA_APP_ID / ADZUNA_APP_KEY
.venv/bin/uvicorn src.main:app --reload
```

## Privacy

Personal data (profile, resume drafts, local DB) never goes into the repo —
see `.gitignore`. Only anonymized seed/example data ships here.

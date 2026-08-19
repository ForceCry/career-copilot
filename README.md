# career-copilot

**A local job-matching service that ranks postings against your actual
profile with vector search, optionally reasons about fit with an LLM, and
drafts the cover letter — a real, working system, not a demo.**

I built this to solve my own problem: too many PHP/Symfony postings to
read by hand, no good way to tell which ones are actually worth an
application. It ingests from three job boards, ranks everything
semantically against my resume, and only spends an LLM call on the
shortlist that's already promising — the expensive reasoning stays cheap
because it isn't doing the sorting.

It's also the real code behind a LinkedIn article series on AI-assisted
engineering — see [`docs/articles/`](docs/articles/). Every design
decision, bug, and architectural U-turn documented there happened for
real while building something I actually use to apply to jobs, and got
written up honestly rather than cleaned up for a demo.

**Deploying this yourself?** Humans: jump to [Setup](#setup) below, or
[`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) for the full walkthrough. Using
an AI coding agent to do the setup for you: point it at
[`AGENTS.md`](AGENTS.md) instead — it's written as directives for an
agent rather than prose for a person.

## Features

- **Multi-source ingestion** — Adzuna, Arbeitnow, justjoin.it, each as an
  independently reusable client library (`libs/`), not tied to this app,
  with an opt-in in-stack scheduler (`supercronic`, off by default) and a
  history of every run (`GET /ingestion-runs`) if you'd rather not
  babysit an external crontab
- **Semantic ranking** — Elasticsearch kNN vector search against your
  profile, not keyword matching
- **Optional LLM reranking** — the top shortlist gets scored for fit an
  embedding can't judge (seniority, overqualification, gaps a similarity
  score misses), via your own local Claude Code session — no API key
  to manage
- **Application tracking** — saved/applied/interviewing/offer/rejected/
  dismissed states with an append-only transition history, a pipeline
  view (`/applications`), and a quick status-select right on the
  recommendations feed
- **Feedback-driven ranking** — dismissing or engaging with postings
  feeds back into future recommendations: companies you've written off
  entirely get excluded, and skills/remote-work/seniority levels you
  consistently dismiss get nudged down — measured against your *own*
  baseline dismiss rate, not a fixed threshold, so a candidate's core
  stack doesn't get penalized just for showing up in every posting
- **Tailored artifacts** — generated cover letters and resume-tailoring
  suggestions per posting, grounded only in what's actually in your
  profile, persisted so past generations stay available from the
  applications pipeline
- **Freshness-aware recommendations** — a posting a source stops
  returning (closed, expired, pulled) drops out of recommendations
  automatically, not just on the next full re-ingest
- **Production-shaped, not a toy** — Alembic migrations, structured
  JSON logging, Prometheus/Grafana monitoring (10-panel dashboard,
  provisioned out of the box), non-root containers, CI across every
  package, 162 automated tests

## Tech stack

FastAPI · MySQL · Elasticsearch · RabbitMQ · Hugging Face
text-embeddings-inference · Alembic · Prometheus + Grafana · Docker
Compose · local Claude Code CLI for LLM reasoning (no API key)

## Repo layout

This is a monorepo: the main service, the three job-board ingestion
libraries it consumes, and the local embedding service all live here, in
one place, one `docker compose up`. They used to be five separate repos
(consumed via sibling directories / a shared cross-project Docker network)
- consolidated for the same reason the article series exists: a reader
should be able to clone one thing and understand the whole system, not
piece it together from five READMEs.

- `src/`, `scripts/`, `alembic/`, `tests/` - the main service
- `libs/adzuna-client`, `libs/arbeitnow-client`, `libs/justjoinit-scraper` -
  the three ingestion sources, each still an independently
  installable/testable package in its own right (own `pyproject.toml`, own
  test suite, own CI job) - not flattened into `src/`, since the whole
  point is that they're usable standalone too
- `infra/` - the local embedding service (Hugging Face
  text-embeddings-inference), merged into this repo's own
  `docker-compose.yml` rather than a separate project

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

Each source's actual fetch/parse logic lives in its own package under
`libs/`, not in `src/` - `AdzunaSource`/`ArbeitnowSource`/`JustJoinItSource`
in `src/ingestion/sources/` are thin adapters mapping each library's own
`Job` model onto this project's `Vacancy` DTO:

- [`libs/adzuna-client`](libs/adzuna-client)
- [`libs/arbeitnow-client`](libs/arbeitnow-client)
- [`libs/justjoinit-scraper`](libs/justjoinit-scraper)

Each is independently installable and testable on its own (own
`pyproject.toml`, own test suite) - see their own READMEs for what was
learned building them against the live APIs (rate limits, salary formats,
what's actually filterable server-side). Living in this repo doesn't
change that; it just means one clone gets you the whole system instead of
four.

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
MySQL, embeds it (`passage: {title}\n\n{description}`) via the `embeddings`
service (`infra/`, this repo's own compose - see Setup below for the
model download), and indexes the vector. This replaced an
earlier keyword-overlap heuristic entirely - free to run, but blind to
synonyms and much cheaper to scale to a large vacancy pool than scoring
every vacancy with an LLM call.

`llm_rerank_top_n=N` (opt-in, 0 by default) is the second stage: sends
only the top N of the vector-search shortlist to an LLM for semantic
reasoning — seniority fit, overqualification risk, gaps a similarity
score alone can't explain.

The LLM call goes through the local `claude` CLI (`claude -p ...`), not the
Anthropic API — it reuses your already-authenticated Claude Code session, no
API key to manage. That means the container needs two files from your
`~/.claude` session mounted in read-only (`.credentials.json` and
`settings.json` - just those two, not the whole directory: confirmed live
that's all the CLI needs non-interactively, and the rest of `~/.claude`
holds conversation history from every other project on the machine, which
the container has no reason to read). If that's not something you want to
grant a container, run `llm_rerank_top_n` locally via `.venv` instead — the
Docker path works without it, just without the LLM rerank layer.

## Setup

```bash
git clone https://github.com/ForceCry/career-copilot.git
cd career-copilot
```

### Docker (recommended)

One `docker compose up` brings up everything - MySQL, Elasticsearch,
RabbitMQ, the embedding service, the API, and the embedding-worker:

```bash
cp .env.example .env  # fill in ADZUNA_APP_ID / ADZUNA_APP_KEY
docker compose up --build -d
docker compose exec api python scripts/ingest.py --source adzuna
curl http://localhost:8000/health
curl "http://localhost:8000/vacancies?keywords=php,symfony"
curl "http://localhost:8000/recommendations?top_k=10&llm_rerank_top_n=5"
```

**First run downloads the embedding model** (`intfloat/multilingual-e5-base`,
~1GB) into `infra/data/hf-cache/` - not committed (gitignored), and not a
separate manual step: the `embeddings` service does this itself on first
startup, and `api`/`embedding-worker` both wait on its healthcheck (which
only passes once the model is loaded) before starting, via `depends_on`.
Expect the first `docker compose up` to take a few minutes longer than
every one after it; nothing to do but wait.

### Local (no Docker)

```bash
uv venv
uv pip install -r requirements.txt
uv pip install -e libs/adzuna-client -e libs/arbeitnow-client -e libs/justjoinit-scraper
cp .env.example .env  # fill in ADZUNA_APP_ID / ADZUNA_APP_KEY
.venv/bin/uvicorn src.main:app --reload
```

Still needs `docker compose up -d mysql elasticsearch rabbitmq embeddings`
for its dependencies even when running the API itself locally.

### Your profile

Out of the box the app seeds a placeholder profile (`profile.example.json`)
so it runs without any real personal data. For recommendations matched
against your actual background, copy it to `profile.local.json` (gitignored)
and fill in your own skills/experience, then run `scripts/seed_profile.py`.
If you're already using an AI coding agent in this repo, you can instead
just hand it your resume and ask it to set your profile up - see
`AGENTS.md`'s profile-setup section for the exact instructions it needs.
Full walkthrough (including troubleshooting): `docs/DEPLOYMENT.md`.

## Monitoring

Prometheus + Grafana, provisioned in the same `docker-compose.yml` as
everything else. Grafana at http://localhost:3000 comes pre-provisioned
with a Prometheus datasource and a `career-copilot` dashboard (10 panels,
including LLM call rate/duration) - no manual setup.

Four scrape targets:
- **RabbitMQ** - built-in Prometheus plugin, enabled via `command:` in
  docker-compose.yml (not on by default even in the -management image).
  Scraped from `/metrics/per-object`, not the default `/metrics` - the
  default path only exposes cluster-wide aggregates with no `queue`
  label, confirmed live; per-queue depth (what the dashboard needs)
  only exists on the per-object path.
- **TEI** (the `embeddings` service, `infra/`) - exposes
  metrics on its main port (80) at `/metrics`, not the separate port
  9000 `--help` suggests - confirmed live (nothing listens on 9000).
- **api** - instrumented with `prometheus-fastapi-instrumentator`
  (request rate/latency/status) plus custom `llm_calls_total`/
  `llm_call_seconds` metrics for the local `claude` CLI calls, all at
  `GET /metrics`.
- **embedding-worker** - custom metrics via `prometheus_client`
  (`embedding_worker_vacancies_processed_total`,
  `embedding_worker_processing_seconds`), served on its own port 9100.
  Only meaningful for a single replica - scaling it (as the backfill
  script's README section describes) means Prometheus's static target
  resolves to whichever replica Docker's DNS round-robin picks that
  scrape, not a sum across all of them.

## Privacy

Personal data (profile, resume drafts, local DB, backups) never goes into
the repo — see `.gitignore`. Only anonymized seed/example data ships here;
verified against the full commit history, not just the current tree,
before this repo went public. Every service is also bound to `127.0.0.1`
only, on purpose — see `docs/DEPLOYMENT.md`'s Privacy and security section
for the full reasoning and what that means if you deploy this somewhere
that isn't your own machine.

## License

[MIT](LICENSE)

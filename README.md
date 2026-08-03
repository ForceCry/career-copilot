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
- **Adzuna** — needs a free `app_id`/`app_key` from
  https://developer.adzuna.com/. Real server-side filtering by keyword
  (`what`) and location (`where`), with `pl` as a supported country code for
  Poland. This is the primary source once keys are in place.

## Setup

### Docker (recommended)

```bash
cp .env.example .env  # fill in ADZUNA_APP_ID / ADZUNA_APP_KEY
docker compose up --build
curl http://localhost:8000/health
curl "http://localhost:8000/vacancies?keywords=php,symfony&location=Warsaw"
```

### Local (no Docker)

```bash
uv venv
uv pip install -r requirements.txt
cp .env.example .env  # fill in ADZUNA_APP_ID / ADZUNA_APP_KEY
.venv/bin/uvicorn src.main:app --reload
```

## Privacy

Personal data (profile, resume drafts, local DB) never goes into the repo —
see `.gitignore`. Only anonymized seed/example data ships here.

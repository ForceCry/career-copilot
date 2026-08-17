# Deployment guide

This is the fuller walkthrough. The README's Setup section is the quick
version; this one covers prerequisites, verification, troubleshooting,
and how to seed your own profile. If you're using an AI coding agent to
do this setup for you, point it at `AGENTS.md` instead — it's written
as directives for an agent, this one is written for you.

## Prerequisites

- Docker + Docker Compose (v2, the `docker compose` subcommand, not the
  standalone `docker-compose` binary)
- ~2GB free disk space (MySQL/Elasticsearch data plus the ~1GB embedding
  model cache)
- Optional: a free [Adzuna](https://developer.adzuna.com/) `app_id` /
  `app_key` if you want that ingestion source. Arbeitnow and justjoin.it
  need no key.
- Optional: an authenticated [Claude Code](https://claude.com/claude-code)
  session on the host, if you want the LLM reranking feature (`GET
  /recommendations?llm_rerank_top_n=N`). Everything else works without
  it.

## First run

```bash
git clone https://github.com/ForceCry/career-copilot.git
cd career-copilot
cp .env.example .env
```

Open `.env` and fill in `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` if you have
them — otherwise leave them blank, you just won't be able to use the
Adzuna source. Everything else in `.env` already has sane local
defaults.

```bash
docker compose up --build -d
```

**This first run takes a few minutes longer than usual** — the
`embeddings` service downloads its model (~1GB) on first startup, and
the API/embedding-worker containers wait for it to be ready before they
start (`depends_on` + healthcheck). You don't need to do anything but
wait; there's no separate "download the model" step to run yourself.

Check everything is up:

```bash
docker compose ps
curl http://localhost:8000/health
# {"status":"ok"}
```

If `/health` returns a 503, it'll tell you which dependency (MySQL or
Elasticsearch) isn't reachable yet — `docker compose logs <service>` for
that one.

## Loading job postings

Nothing is fetched automatically. Run at least one source:

```bash
docker compose exec api python scripts/ingest.py --source arbeitnow
docker compose exec api python scripts/ingest.py --source adzuna       # needs the API key above
docker compose exec api python scripts/ingest.py --source justjoinit   # slower, run less often
```

Then confirm:

```bash
curl "http://localhost:8000/vacancies?keywords=php"
```

If this comes back empty, ingest didn't find anything for that keyword
— try a broader `--keywords` value on the ingest command, or check
`docker compose logs embedding-worker` for errors.

## Setting up your own profile

Out of the box, the app seeds a placeholder profile (`profile.example.json`
— "Jane Doe") so it runs without any personal data. To get recommendations
matched against *your* actual background, you need a `profile.local.json`
in the repo root.

**Option A — by hand.** Copy `profile.example.json` to `profile.local.json`
and edit it. The fields are self-explanatory except:
- `skills[].category` is one of `language` / `framework` / `tool` / `concept`
- `experiences[].highlights` is one string with `\n` between bullet points, not a list
- `end_date: null` means "current position"

**Option B — hand your resume to an AI coding agent.** If you're already
using Claude Code, Cursor, or similar in this repo, ask it to read your
resume and set up your profile — the exact instructions it needs are in
`AGENTS.md`'s "Setting up the user's profile" section. This is usually
faster and less error-prone than hand-transcribing a resume into JSON.

Either way, once `profile.local.json` exists:

```bash
docker compose exec api python scripts/seed_profile.py
curl http://localhost:8000/profile   # should show your data, not Jane Doe
```

Rerun `seed_profile.py` any time you edit `profile.local.json` — it
replaces the seeded profile in place (single-user tool, so there's
always exactly one).

`profile.local.json` is gitignored. It holds real personal data and
should never be committed — this is true whether you're running this
privately or maintaining a public fork.

## Trying it out

```bash
curl "http://localhost:8000/recommendations?top_k=10"
```

Or open http://localhost:8000/ in a browser for the HTML view — same
data, with cover-letter/tailoring buttons per result.

For LLM-reranked results (`llm_rerank_top_n>0`, in the URL or the HTML
form), the `api` container needs your Claude Code session mounted in —
see `docker-compose.yml`'s `api.volumes` section. If you don't want to
grant a container access to that, run the API locally instead (`uv venv
&& uv pip install -r requirements.txt && .venv/bin/uvicorn src.main:app
--reload`, with the rest of the stack still in Docker) — the reranking
call then runs as your own user, same as any other local script.

## Monitoring

Grafana at http://localhost:3000 (no login — anonymous admin access,
acceptable only because everything is bound to `127.0.0.1`; see the
Privacy/security section below before changing that). Prometheus at
http://localhost:9090.

## Backups

```bash
./scripts/backup_db.sh
```

Dumps the database to `backups/` (gzip-compressed, gitignored, `0600`
permissions), keeping the last 14. Not scheduled automatically — wire it
into cron yourself if you want that.

## Privacy and security notes

- Every port in `docker-compose.yml` is bound to `127.0.0.1`, not
  `0.0.0.0` — this is deliberate. The stack handles candidate PII, has
  an unauthenticated API that can trigger paid LLM calls, and runs
  Grafana with anonymous admin access. None of that is meant to be
  reachable from your LAN, let alone the internet. If you need remote
  access, put a real reverse proxy with authentication in front of it —
  don't just change the bind address.
- What actually lands in the database (all local, gitignored, covered by
  `scripts/backup_db.sh`): the profile you seed from `profile.local.json`
  (name/email/phone/experience), saved `ResumeVersion` snapshots, freeform
  notes/follow-up dates on tracked applications (`Application.notes`),
  and every generated cover letter/tailoring-suggestions artifact
  (`GeneratedArtifact.content`) — these are persisted, not request-time-
  only. None of it is written to git; `git status --ignored` is the way
  to confirm that for yourself in a fork.
- `.env`, `profile.local.json`, `resume.local.md`, `data/` (including
  the embedding model cache), and `backups/` are all gitignored. If
  you're maintaining a fork or contributing back, double-check `git
  status --ignored` before pushing rather than assuming.
- The `claude` CLI calls (LLM reranking, cover letters) run with your
  real, authenticated Claude Code session mounted read-only into the
  container. That session can incur real cost per call — there's a
  process-wide concurrency cap (2 concurrent calls) and a hard cap on
  `llm_rerank_top_n` (10) as the only things standing between normal use
  and an accidental cost spike, since this is a single-user tool with no
  auth in front of it.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `docker compose up` hangs on `embeddings` for a long time on first run | Expected — downloading the ~1GB model. Check `docker compose logs embeddings` for progress, not failure. |
| `/health` returns 503 | MySQL or Elasticsearch isn't healthy yet — `docker compose ps`, then `docker compose logs` on whichever one is unhealthy. |
| `/recommendations` returns empty | No vacancies ingested yet, or none score above `min_score` — run `scripts/ingest.py` for at least one source first. |
| `/recommendations?llm_rerank_top_n=...` errors | The `~/.claude` mount is missing or your Claude Code session isn't authenticated on the host. Everything else in the app works without it. |
| Adzuna ingest returns 0 results | Missing or invalid `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` in `.env` — check `docker compose logs api` for the actual API error. |

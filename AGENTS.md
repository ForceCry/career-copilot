# AGENTS.md

Directives for an AI coding agent setting up or operating this project on
someone's behalf. If you're a human, read `README.md` and
`docs/DEPLOYMENT.md` instead — this file is written as instructions for
an agent, not prose for a person.

## What this is

career-copilot is a local, single-user job-matching service: it ingests
job postings, ranks them against one person's profile via vector search
(+ optional LLM reranking), and generates tailored cover letters/resume
edits. Everything runs in Docker on the user's own machine. Nothing is
multi-tenant; there is exactly one profile.

## Bringing the stack up

0. If you don't already have this repo checked out: `git clone
   https://github.com/ForceCry/career-copilot.git && cd career-copilot`.
1. Confirm Docker and Docker Compose are available (`docker compose
   version`). If not, stop and tell the user - don't attempt to install
   Docker yourself.
2. `cp .env.example .env` if `.env` doesn't already exist. Leave
   `ADZUNA_APP_ID`/`ADZUNA_APP_KEY` blank unless the user gives you
   values - the Adzuna source just won't work without them, everything
   else (Arbeitnow, justjoin.it, the API itself) works fine without any
   keys.
3. `docker compose up --build -d` from the repo root.
4. **The first run downloads an embedding model (~1GB) into
   `infra/data/hf-cache/`.** This happens automatically - the
   `embeddings` service does it on its own, and `api`/`embedding-worker`
   both wait on its healthcheck before starting. Expect several minutes
   longer than every subsequent `up`. Don't interrupt it; don't try to
   pre-fetch the model yourself.
5. Verify: `curl http://localhost:8000/health` should return
   `{"status":"ok"}`. If it doesn't after a few minutes, run `docker
   compose ps` and `docker compose logs <service>` for whichever
   container isn't healthy before doing anything else.
6. The DB has no vacancies yet. Run at least one ingest before expecting
   `/recommendations` to return anything:
   ```
   docker compose exec api python scripts/ingest.py --source arbeitnow
   ```
   (No API key needed for this one - good first check. Add `--source
   adzuna` / `--source justjoinit` once profile setup below is done.)

## Setting up the user's profile

This is the step a human would otherwise do by hand-editing JSON. Do it
for them from their resume instead:

1. Ask the user for their resume (a pasted-in text, a file path, or a
   PDF/DOCX to read).
2. Read `profile.example.json` at the repo root - it's the authoritative
   schema (fields present, their types, what a realistic value looks
   like). Field notes that aren't obvious from the example alone:
   - `skills[].category` - one of `"language"`, `"framework"`, `"tool"`,
     or `"concept"`. Pick the closest fit; don't invent new categories.
   - `experiences[].highlights` - a single string with `\n` separating
     bullet points, not a list/array.
   - `start_date`/`end_date` - ISO `YYYY-MM-DD`. If the resume only gives
     a month/year, use the 1st of that month. `end_date: null` means
     "current position" - use that for the most recent role if the
     resume says "Present"/"current"/similar, don't guess a date.
   - `summary` - a short paragraph, not the whole resume.
3. Map the resume onto this schema and write the result to
   `profile.local.json` at the repo root (sibling to
   `profile.example.json`, not inside `src/`). **Don't invent anything
   not in the source resume** - if a field isn't present (no GitHub URL,
   no phone number), leave it as an empty string rather than fabricating
   one. If something is genuinely ambiguous (overlapping employment
   dates, an unclear job title), ask the user rather than guessing.
4. Run the seed script:
   ```
   docker compose exec api python scripts/seed_profile.py
   ```
   (or `.venv/bin/python scripts/seed_profile.py` if running locally
   without Docker). It replaces whatever profile is currently seeded -
   safe to rerun after editing `profile.local.json` again.
5. Verify: `curl http://localhost:8000/profile` should reflect the data
   you just wrote, not the `Jane Doe` example.

`profile.local.json` is gitignored - it holds real personal data and
must never be committed. Don't remove it from `.gitignore`, don't `git
add -f` it, and don't paste its contents into a commit message, issue,
or PR description.

## Verifying the whole thing works end to end

```
curl "http://localhost:8000/recommendations?top_k=10"
curl "http://localhost:8000/recommendations?top_k=10&llm_rerank_top_n=3"  # needs ~/.claude mounted, see below
```
If the first returns results but scores look uniformly low or empty,
re-check that `scripts/ingest.py` actually ran for at least one source
and that `docker compose logs embedding-worker` shows vacancies being
indexed, not erroring.

## Known gotchas

- **LLM reranking (`llm_rerank_top_n>0`) needs the user's own Claude Code
  session mounted in** (see `docker-compose.yml`'s `api` service volumes
  - two specific files under `~/.claude`, not the whole directory).
  Without it, that endpoint/UI option will error - the rest of the app
  works fine regardless.
- Ports are bound to `127.0.0.1` on purpose (candidate PII, an
  unauthenticated API that triggers paid LLM calls, and an anonymous-
  admin Grafana instance all live on this stack). Don't rebind any of
  them to `0.0.0.0` "to make it easier to test" without asking first -
  that's a real exposure change, not a convenience tweak.
- `infra/data/hf-cache/` and `data/` more generally are gitignored and
  can be large (the model cache alone is ~1GB). Don't try to commit them
  even if a broad `git add -A` would otherwise pick them up.

## Before pushing anywhere public

If you're helping set this repo up as a public GitHub repo (not just
running it locally): confirm with the user before the actual `git push`
or repo-creation step, same as any other action that publishes something
externally. Re-check `.env`, `profile.local.json`, and `backups/*.sql.gz`
are all still gitignored and not staged - `git status --ignored` should
show them, `git ls-files` should not.

## Where to look for more

- `README.md` - architecture, ingestion sources, matching pipeline
- `docs/DEPLOYMENT.md` - the fuller human-oriented setup/troubleshooting guide
- `docs/articles/` - narrative write-ups of specific design decisions and bugs found along the way

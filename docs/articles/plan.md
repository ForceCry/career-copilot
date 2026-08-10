# LinkedIn article series — plan

Real-code case study backing a technical article series on AI-assisted
engineering, grounded in what was actually built/found/decided in this
repo (and its now-merged sibling repos) — not generic AI-and-coding
takes. Language: English.

## 1. "I Asked an AI to Design and Build a Microservice. Here's What Actually Happened."

*(intro — draft in `01-intro.md`)*

- Architecture overview: FastAPI + MySQL + Elasticsearch vector search +
  RabbitMQ/worker + LLM reranking via a local `claude` CLI session, not
  an API key
- Framing: the full engineering loop — architecture, implementation,
  independent review, roadmap planning — not just "AI wrote some code"
- Concrete numbers: 5 repos consolidated into 1, 2 full rounds of
  independent review, 40+ tests, several instrumentation bugs caught
  post-hoc
- Sets up the rest of the series

## 2. "Second AI as Reviewer of the First" (multi-agent review)

- The Codex-review workflow: Claude builds, Codex independently reviews
  as a skeptical senior architect - raw findings shown to the user
  as-is, not pre-filtered
- Concrete rejected finding: a hypothesis about Adzuna API AND-semantics,
  disproven with a live API call - not every AI suggestion survives
  verification
- Thesis: the value isn't "two AIs agree" - it's that one model's blind
  spots get caught by a differently-biased second opinion, and a human
  makes the final call

## 3. "Monitoring Is Still Code, and It Still Has Bugs" (observability)

- Adding LLM call metrics/structured logging surfaced three compounding
  bugs: silent failure paths with zero logging, an uncaught
  `subprocess.TimeoutExpired` that could crash a whole request, and
  Alembic's own logging setup silently wiping the app's structured-log
  config on every startup
- A live credential leak: enabling INFO-level logging activated httpx's
  own request logging, which printed live API keys straight into the
  request URL
- Thesis: instrumentation code isn't lower-risk than feature code - it
  needs the same live-verification discipline

## 4. "Retrofitting Migrations Onto a Live Database Without Downtime" (Alembic)

- The problem: schema had been evolving via hand-run `ALTER TABLE`
  statements, no single source of truth
- The technique: generate a baseline migration against an empty scratch
  database, diff it byte-for-byte against the live schema to prove they
  match, then `stamp` the live DB instead of replaying the migration
- A bug caught in the process: Alembic's autogenerate omits `import
  sqlmodel` for AutoString columns - would have raised `NameError` on
  the first real run
- Thesis: adopting a "best practice" tool onto an already-running system
  is its own engineering problem, not a `pip install`

## 5. "From Five Repos to One: When 'Best Practice' Isn't the Right Call" (monorepo)

- Codex's roadmap suggested pinning the sibling repos as versioned
  dependencies - the textbook-correct answer
- The actual decision: merge everything into one repo instead - solves
  the same underlying problem (unreproducible builds) more simply and
  matches the project's real goal (clone one thing, understand the whole
  system)
- The `~/.claude` mount-narrowing story: giving an agent your credentials
  means thinking about blast radius - narrowed from the whole
  conversation-history directory to the two files the CLI actually
  needs, verified live that cost/behavior didn't change
- Thesis: textbook practice isn't automatically the right call - what
  the project is *for* should drive architecture decisions

## 6. "A Fix That Worked for One Example and Didn't for the Eighth" (freshness bug)

- A real bug from actual usage: a 404'd job posting was still the #1
  recommendation
- The investigation before the fix: proving the posting was genuinely
  dead (direct request, sitemap check) rather than guessing
- A proportionate fix: a query-time freshness filter, not a full
  ingestion-run-tracking system
- The honest result: auditing all 647 vacancies after the fix found 9
  dead links - the fix caught 8, but one slipped through because the
  *source itself* still listed it as active
- Thesis: "works for the example I tested" and "actually fixed" are
  different claims - auditing after the fix is part of the job, not
  paranoia

## 7. "What's Left After N Hours of Building With Two AIs" (retrospective, optional)

- The roadmap-comparison pattern: asking both Claude and Codex
  independently for a development roadmap and presenting both side by
  side instead of picking one
- Cross-cutting lessons: verify before fixing, show raw findings not
  just summaries, don't over-engineer past the tool's actual scale (a
  personal single-user tool, not enterprise SaaS)
- What's deliberately still deferred, and why (application tracking,
  index/ES reconciliation)

# I Asked an AI to Design and Build a Microservice. Here's What Actually Happened.

Two weeks ago I gave Claude a vague idea: I wanted something to help me sort through PHP/Symfony job postings without manually re-reading fifty listings a day. What came out the other end wasn't a script. It's a real, running service — FastAPI backend, MySQL, an Elasticsearch vector search index, a RabbitMQ-backed async worker, Prometheus and Grafana watching it all, and an LLM reranking layer that doesn't call an API at all — it shells out to my own already-authenticated Claude Code session.

I didn't write most of the code. I made the calls. This is the first post in a series about what that actually looked like — not the sanitized version, the real one, bugs and all.

**What it does**

career-copilot pulls job postings from three sources (Adzuna, Arbeitnow, justjoin.it), embeds them semantically, ranks them against my profile via kNN vector search in Elasticsearch, optionally reranks the top candidates with an LLM for things a similarity score can't tell you — seniority mismatch, overqualification risk, a skills gap in the fine print — and generates tailored cover letters and resume edits for postings I actually want to apply to.

*[Architecture diagram: ingestion → async embedding pipeline → semantic matching, see `diagrams/architecture-overview.drawio` / insert exported PNG here]*

None of that is novel. What I want to talk about in this series is *how* it got built, because "I used AI to write code" undersells what actually happened and overclaims in the wrong direction at the same time.

**The part that's easy to miss**

Most AI-and-coding content stops at "the AI wrote a function." What actually happened here was closer to running a small engineering team:

- Architecture decisions got made and revisited — MySQL over SQLite once the data outgrew a single file, an outbox-lite pattern once we discovered a queue publish could silently fail and leave data unrecoverable, Alembic retrofitted onto a database that had been evolving by hand.
- A *second* AI — Codex — independently reviewed the work as a skeptical senior engineer, twice, across every repo. Not as a rubber stamp: some of its findings turned out to be wrong, and got rejected after I verified them against the real, live systems rather than taking either AI's word for it.
- Both AIs were asked, independently, for a development roadmap. I didn't pick one and hide the other — I put them side by side and made the call myself.
- Five separate repositories got consolidated into one, not because a checklist said to, but because the "textbook correct" fix (pin your dependencies) solved a smaller problem than the one that actually mattered.
- A real bug reached production-shaped use: a closed job posting kept surfacing as the #1 recommendation days after it 404'd. Finding it, proving it, and fixing it proportionately — not by bolting on a whole subsystem — is its own story.

**Some numbers, because "AI helped me code" is meaningless without them**

- 5 repositories consolidated into 1, with the reasoning for *why* documented, not just done
- 2 full rounds of independent code review, with every finding shown raw before any of it got acted on
- 40+ automated tests, several of which exist specifically because a bug was caught *after* the code looked done
- At least three cases where instrumentation code — logging, metrics, the stuff meant to make the system safer to operate — had its own real bugs, including one that would have silently disabled all logging on every restart, and one that leaked live API credentials into container logs

That last category is the one nobody talks about, and it's where this series is going to spend real time.

**What's coming**

Over the next few posts I'll walk through specific, concrete pieces of this:

- What it actually looks like to have a second AI independently review the first one's work — including the times it was wrong
- How "add monitoring" turned into finding three separate bugs, one of them a credential leak
- Retrofitting database migrations onto a live system without downtime, and the one-line bug that would have broken it on the first real run
- Why the "correct" software engineering practice — pin your dependencies — turned out to be the wrong call here, and what replaced it
- A bug report, an investigation, a fix, and then an audit that proved the fix was only 8/9ths complete — and why saying that honestly matters more than claiming victory

None of this is a demo. It's a real tool I use to apply to real jobs, built mostly through conversation, reviewed by a skeptical second opinion, and — where it mattered — verified against reality before I trusted it. That combination is the actual story, not "AI wrote my code."

*Next up: what it's like to have Codex tell Claude it's wrong — and the times it actually was.*

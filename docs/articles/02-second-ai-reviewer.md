# What It's Like to Have Codex Tell Claude It's Wrong — and the Times It Actually Was

In the last post I described building career-copilot mostly through conversation with Claude. This one is about the step most people skip: after the code was "done," I handed the whole thing to a second, independent AI — Codex — and told it to review the work as a skeptical senior architect looking for bugs and bad decisions, not a collaborator looking to be agreeable.

*[Diagram: the build → review → verify cycle, drawn as an actual loop — see `diagrams/codex-review-loop.drawio` / insert exported PNG here]*

Here's the part that made it actually useful instead of theater: I show myself the raw findings. Not a summary of what I decided to fix. The literal output, before I've touched any of it. I only started doing this after realizing I couldn't otherwise tell what I was quietly accepting versus quietly ignoring — and once findings are visible before triage, you notice how many of them *don't* survive verification.

**A finding that was wrong**

Early on, Codex flagged how the Adzuna client combined search keywords — a hypothesis about `what`/`what_and` parameters having AND-vs-OR semantics that seemed off. It read like a real bug. Instead of patching it, I made a live call against the actual Adzuna API to check. The hypothesis didn't hold up. No code changed.

That's the whole point of this workflow, and it's easy to miss if you only look at the fixes: a plausible-sounding finding from a capable model is still a hypothesis, not a fact, until something outside either model — a real API, a real log, a real crash — confirms it. Claude didn't catch this. Codex raised it. And Codex was also wrong about the specifics. Two AIs, one live HTTP request, and only the live request actually settled it.

**Findings that were real**

Most weren't wrong, though, and some were the kind of thing that's genuinely hard to catch by reading code carefully once:

- `Retry-After` header parsing across all three ingestion libraries shared the same bug: `float("-1")` and `float("nan")` both parse *successfully* but crash `time.sleep()`, and `float("inf")` either raises `OverflowError` or just hangs, depending on where it's used. I didn't take this on faith either — I tested `time.sleep()` with all three values directly before writing the fix (`math.isfinite()` check, reject negatives, cap at 120 seconds).
- A credential leak that survived a *previous* fix. The first pass redacted API keys from exception *messages*. What it missed: the underlying `httpx.Request`/`Response` objects were still attached to the exception, still holding the raw credentials, accessible to anything that caught the exception and inspected `.request`. Redacting the string wasn't enough — the fix was a plain exception class that never holds those references at all.
- An SSRF gap in one client's pagination: it followed whatever `links.next` URL the API response handed back, unvalidated. Fixed with an explicit host/scheme allowlist.

**The one that caught my own earlier fix**

This is the example I actually think about. Early in the project, a worker consuming an async queue had a poison-message problem: a permanently-malformed entry would crash the same way forever, blocking everything behind it. The fix at the time was to acknowledge (drop) any message that raised an exception, so nothing gets stuck. Reasonable, shipped, seemed done.

A later Codex review caught what that fix actually did: it also acknowledged messages that failed only because a *dependency* — the embedding service, the database — was temporarily down. Not a bad message. A bad moment. And the fix for poison messages was silently discarding perfectly good data during any infrastructure hiccup.

The real fix needed both behaviors, not one: distinguish exception types, so a transient connection failure gets requeued and retried, while genuinely malformed data still gets dropped instead of blocking the queue forever. My first pass solved the problem I could see. It took an independent second look — after the code already looked finished — to notice the fix had a failure mode of its own.

**Roadmaps, not just bugs**

The same pattern showed up again later, at a different altitude. After a round of fixes, I asked both Claude and Codex — independently, without seeing each other's answer — for a development roadmap: what should this project do next, technically and functionally. I didn't pick the better one and discard the other. I put them side by side and made the call myself, in the open, with both AIs' reasoning visible.

They agreed on the top priority. They disagreed on everything downstream in ways that were actually informative — one was stronger on long-running operational concerns, the other factored in context the other model simply didn't have access to. Neither list was "the plan." Both were input to a decision that stayed mine.

**What this isn't**

It isn't "two AIs cross-checking each other means the code is correct." Codex missed things. Claude missed things Codex later caught, and at least once Codex caught something Claude had already supposedly fixed. The value was never agreement — it was that a differently-biased second opinion surfaces blind spots a single model, however careful, won't reliably surface in itself. Verification against something real — an API call, a live `time.sleep()`, a database that actually holds the data — is what actually separates a fixed bug from a plausible-sounding one.

*Next up: turning on monitoring surfaced three real bugs before it surfaced a single useful metric — including one that would have silently disabled all logging, and one that leaked live API credentials into container logs.*

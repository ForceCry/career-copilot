import json
import logging
import re
import subprocess
import threading
import time
from pathlib import Path

from pydantic import BaseModel

from ..ingestion.models import Vacancy
from ..observability import LLM_CALL_DURATION, LLM_CALLS
from ..storage.models import Profile

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Run from PROJECT_ROOT specifically: invoking `claude` from an unrelated
# cwd pulls that directory's CLAUDE.md/memory/skills into context and
# inflates cost ~10x per call (measured: $0.0024 from a clean project dir
# vs $0.025 from one with unrelated project memory loaded).
SYSTEM_PROMPT = (
    "You are a job-matching assistant. Given a candidate profile and a job "
    "posting, output ONLY a single JSON object: "
    '{"score": <0-100 integer>, "reasoning": "<1-2 sentences>", '
    '"concerns": ["<short phrase>", ...]}. No markdown fences, no prose '
    "outside the JSON."
)

JSON_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)

# FastAPI runs sync endpoints in a threadpool, so concurrent HTTP requests
# each rerank-ing a shortlist would otherwise spawn unboundedly many
# `claude` subprocesses at once - real cost and real CPU/RAM per call,
# with no auth in front of this single-user tool to limit request rate.
# Caps total concurrent LLM calls process-wide rather than per-request.
# Flagged by an independent Codex review.
_CONCURRENCY_LIMIT = threading.Semaphore(2)


class LlmMatchResult(BaseModel):
    vacancy_id: int
    vacancy_title: str
    vacancy_company: str
    vacancy_url: str
    score: int
    reasoning: str
    concerns: list[str] = []


def _build_prompt(vacancy: Vacancy, profile: Profile) -> str:
    experience_lines = "\n".join(
        f"- {e.title} at {e.company} ({e.start_date}–{e.end_date or 'present'}): {e.highlights}"
        for e in profile.experiences
    )
    skills = ", ".join(s.name for s in profile.skills)

    return f"""CANDIDATE PROFILE
Summary: {profile.summary}
Skills: {skills}
Experience:
{experience_lines}

JOB POSTING
Title: {vacancy.title}
Company: {vacancy.company}
Location: {vacancy.location} (remote={vacancy.remote})
Description: {vacancy.description[:3000]}
"""


def _extract_json(raw: str) -> dict:
    fence_match = JSON_FENCE_PATTERN.search(raw)
    content = fence_match.group(1) if fence_match else raw
    return json.loads(content)


_OPERATION = "score_vacancy"


def llm_score_vacancy(
    vacancy_id: int, vacancy: Vacancy, profile: Profile, model: str = "haiku", timeout: float = 30.0
) -> LlmMatchResult | None:
    prompt = _build_prompt(vacancy, profile)

    start = time.monotonic()
    try:
        with _CONCURRENCY_LIMIT:
            process = subprocess.run(
                [
                    "claude", "-p", prompt,
                    "--output-format", "json",
                    "--model", model,
                    "--allowedTools", "",
                    "--system-prompt", SYSTEM_PROMPT,
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=PROJECT_ROOT,
            )
    except subprocess.TimeoutExpired:
        LLM_CALL_DURATION.labels(operation=_OPERATION).observe(time.monotonic() - start)
        LLM_CALLS.labels(operation=_OPERATION, outcome="timeout").inc()
        logger.warning(
            "llm call timed out",
            extra={"operation": _OPERATION, "vacancy_id": vacancy_id, "timeout": timeout},
        )
        return None
    LLM_CALL_DURATION.labels(operation=_OPERATION).observe(time.monotonic() - start)

    if process.returncode != 0:
        LLM_CALLS.labels(operation=_OPERATION, outcome="nonzero_exit").inc()
        logger.warning(
            "llm call exited non-zero",
            extra={
                "operation": _OPERATION, "vacancy_id": vacancy_id,
                "returncode": process.returncode, "stderr": process.stderr[:500],
            },
        )
        return None

    # --output-format json should always produce valid JSON on stdout, but
    # this shells out to an external CLI - previously unguarded, so any
    # unexpected stdout content would crash the whole request with an
    # uncaught JSONDecodeError instead of just failing this one vacancy.
    try:
        envelope = json.loads(process.stdout)
    except json.JSONDecodeError:
        LLM_CALLS.labels(operation=_OPERATION, outcome="bad_stdout").inc()
        logger.warning(
            "llm call produced non-JSON stdout",
            extra={"operation": _OPERATION, "vacancy_id": vacancy_id, "stdout": process.stdout[:500]},
        )
        return None

    if envelope.get("is_error"):
        LLM_CALLS.labels(operation=_OPERATION, outcome="api_error").inc()
        logger.warning(
            "llm call reported an error",
            extra={
                "operation": _OPERATION, "vacancy_id": vacancy_id,
                "result": str(envelope.get("result"))[:500],
            },
        )
        return None

    try:
        payload = _extract_json(envelope["result"])
    except (json.JSONDecodeError, KeyError):
        LLM_CALLS.labels(operation=_OPERATION, outcome="parse_error").inc()
        logger.warning(
            "llm call result did not parse as the expected JSON",
            extra={
                "operation": _OPERATION, "vacancy_id": vacancy_id,
                "result": str(envelope.get("result"))[:500],
            },
        )
        return None

    LLM_CALLS.labels(operation=_OPERATION, outcome="success").inc()
    return LlmMatchResult(
        vacancy_id=vacancy_id,
        vacancy_title=vacancy.title,
        vacancy_company=vacancy.company,
        vacancy_url=vacancy.url,
        score=int(payload.get("score", 0)),
        reasoning=payload.get("reasoning", ""),
        concerns=payload.get("concerns", []),
    )


def llm_rerank(
    vacancies: list[tuple[int, Vacancy]], profile: Profile, top_n: int = 10
) -> list[LlmMatchResult]:
    """Two-stage pipeline: the heuristic scorer already ordered `vacancies`
    cheaply, so only the top_n shortlist pays for an LLM call - a rerank
    step, not a replacement for the first pass. Takes (id, Vacancy) pairs
    rather than bare Vacancy objects since two postings can share a url
    (e.g. a re-post on the same board) - keying by id instead avoids
    conflating them, flagged by an independent Codex review."""
    results = []
    for vacancy_id, vacancy in vacancies[:top_n]:
        result = llm_score_vacancy(vacancy_id, vacancy, profile)
        if result:
            results.append(result)
    return sorted(results, key=lambda r: r.score, reverse=True)

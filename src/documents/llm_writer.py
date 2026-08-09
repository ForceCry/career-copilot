import json
import logging
import subprocess
import time
from pathlib import Path

from ..ingestion.models import Vacancy
from ..observability import LLM_CALL_DURATION, LLM_CALLS
from ..storage.models import Profile

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

COVER_LETTER_SYSTEM_PROMPT = (
    "You are an expert career coach writing cover letters. Write a concise, "
    "specific, non-generic cover letter (250-350 words), plain text, no "
    "markdown formatting, no bracketed placeholders. Ground every claim in "
    "the candidate's actual experience below - never invent anything not "
    "present in the profile. If you sign off with contact details, use "
    "EXACTLY the email and phone given in the CANDIDATE PROFILE section "
    "below and nothing else - do not substitute any other email or phone "
    "number, including any you may otherwise know about the current user. "
    "Output ONLY the letter body, no preamble."
)

TAILORING_SYSTEM_PROMPT = (
    "You are a resume coach. Given a candidate's resume data and a target "
    "job posting, suggest concrete, specific edits to better tailor the "
    "resume to this role: which existing bullets to emphasize or reorder, "
    "what truthful detail to add, what to de-emphasize. Output a short "
    "bullet list (max 8 bullets), plain text, no markdown headers. Never "
    "invent experience the candidate doesn't have - only reframe or "
    "reprioritize what's already there. Output ONLY the bullet list."
)


def _build_context(vacancy: Vacancy, profile: Profile) -> str:
    experience_lines = "\n".join(
        f"- {e.title} at {e.company} ({e.start_date}–{e.end_date or 'present'}): {e.highlights}"
        for e in profile.experiences
    )
    skills = ", ".join(s.name for s in profile.skills)

    return f"""CANDIDATE PROFILE
Name: {profile.full_name}
Email: {profile.email}
Phone: {profile.phone}
Summary: {profile.summary}
Skills: {skills}
Experience:
{experience_lines}

JOB POSTING
Title: {vacancy.title}
Company: {vacancy.company}
Description: {vacancy.description[:3000]}

If any contact details are included in your response, use only the Email
and Phone given above - not any other contact info you may know from
elsewhere.
"""


def _run_claude(
    operation: str, prompt: str, system_prompt: str, model: str = "sonnet", timeout: float = 60.0
) -> str:
    # Run from PROJECT_ROOT, same reasoning as matching/llm_scorer.py: an
    # unrelated cwd pulls its own memory/CLAUDE.md into context and
    # inflates cost. sonnet, not haiku, for writing quality here - unlike
    # scoring, the output is user-facing prose.
    #
    # Unlike llm_score_vacancy (which swallows failures to None, since a
    # missed rerank result just means one fewer shortlist entry), this
    # stays exception-raising - a cover letter/tailoring request has
    # nothing useful to return on failure, so surfacing a 500 is more
    # honest than silently returning empty content. It's now observed
    # (metric + structured log) before re-raising, whereas previously a
    # failure here (including a timeout, uncaught before this fix) left no
    # trace anywhere.
    start = time.monotonic()
    try:
        process = subprocess.run(
            [
                "claude", "-p", prompt,
                "--output-format", "json",
                "--model", model,
                "--allowedTools", "",
                "--system-prompt", system_prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=PROJECT_ROOT,
        )
    except subprocess.TimeoutExpired:
        LLM_CALL_DURATION.labels(operation=operation).observe(time.monotonic() - start)
        LLM_CALLS.labels(operation=operation, outcome="timeout").inc()
        logger.warning("llm call timed out", extra={"operation": operation, "timeout": timeout})
        raise
    LLM_CALL_DURATION.labels(operation=operation).observe(time.monotonic() - start)

    try:
        process.check_returncode()
    except subprocess.CalledProcessError:
        LLM_CALLS.labels(operation=operation, outcome="nonzero_exit").inc()
        logger.warning(
            "llm call exited non-zero",
            extra={"operation": operation, "returncode": process.returncode, "stderr": process.stderr[:500]},
        )
        raise

    try:
        envelope = json.loads(process.stdout)
    except json.JSONDecodeError:
        LLM_CALLS.labels(operation=operation, outcome="bad_stdout").inc()
        logger.warning(
            "llm call produced non-JSON stdout",
            extra={"operation": operation, "stdout": process.stdout[:500]},
        )
        raise

    if envelope.get("is_error"):
        LLM_CALLS.labels(operation=operation, outcome="api_error").inc()
        logger.warning(
            "llm call reported an error",
            extra={"operation": operation, "result": str(envelope.get("result"))[:500]},
        )
        raise RuntimeError(f"claude CLI error: {envelope.get('result')}")

    LLM_CALLS.labels(operation=operation, outcome="success").inc()
    return envelope["result"].strip()


def generate_cover_letter(vacancy: Vacancy, profile: Profile) -> str:
    return _run_claude("cover_letter", _build_context(vacancy, profile), COVER_LETTER_SYSTEM_PROMPT)


def suggest_resume_tailoring(vacancy: Vacancy, profile: Profile) -> str:
    return _run_claude("tailoring_suggestions", _build_context(vacancy, profile), TAILORING_SYSTEM_PROMPT)

import json
import subprocess
from pathlib import Path

from ..ingestion.models import Vacancy
from ..storage.models import Profile

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


def _run_claude(prompt: str, system_prompt: str, model: str = "sonnet", timeout: float = 60.0) -> str:
    # Run from PROJECT_ROOT, same reasoning as matching/llm_scorer.py: an
    # unrelated cwd pulls its own memory/CLAUDE.md into context and
    # inflates cost. sonnet, not haiku, for writing quality here - unlike
    # scoring, the output is user-facing prose.
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
    process.check_returncode()

    envelope = json.loads(process.stdout)
    if envelope.get("is_error"):
        raise RuntimeError(f"claude CLI error: {envelope.get('result')}")
    return envelope["result"].strip()


def generate_cover_letter(vacancy: Vacancy, profile: Profile) -> str:
    return _run_claude(_build_context(vacancy, profile), COVER_LETTER_SYSTEM_PROMPT)


def suggest_resume_tailoring(vacancy: Vacancy, profile: Profile) -> str:
    return _run_claude(_build_context(vacancy, profile), TAILORING_SYSTEM_PROMPT)

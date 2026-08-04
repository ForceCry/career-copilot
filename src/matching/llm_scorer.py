import json
import re
import subprocess
from pathlib import Path

from pydantic import BaseModel

from ..ingestion.models import Vacancy
from ..storage.models import Profile

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


class LlmMatchResult(BaseModel):
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


def llm_score_vacancy(
    vacancy: Vacancy, profile: Profile, model: str = "haiku", timeout: float = 30.0
) -> LlmMatchResult | None:
    prompt = _build_prompt(vacancy, profile)

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
    if process.returncode != 0:
        return None

    envelope = json.loads(process.stdout)
    if envelope.get("is_error"):
        return None

    try:
        payload = _extract_json(envelope["result"])
    except (json.JSONDecodeError, KeyError):
        return None

    return LlmMatchResult(
        vacancy_title=vacancy.title,
        vacancy_company=vacancy.company,
        vacancy_url=vacancy.url,
        score=int(payload.get("score", 0)),
        reasoning=payload.get("reasoning", ""),
        concerns=payload.get("concerns", []),
    )


def llm_rerank(vacancies: list[Vacancy], profile: Profile, top_n: int = 10) -> list[LlmMatchResult]:
    """Two-stage pipeline: the heuristic scorer already ordered `vacancies`
    cheaply, so only the top_n shortlist pays for an LLM call - a rerank
    step, not a replacement for the first pass."""
    results = []
    for vacancy in vacancies[:top_n]:
        result = llm_score_vacancy(vacancy, profile)
        if result:
            results.append(result)
    return sorted(results, key=lambda r: r.score, reverse=True)

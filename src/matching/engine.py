from pydantic import BaseModel

from ..ingestion.models import Vacancy

POINTS_PER_MATCHED_SKILL = 15


class MatchResult(BaseModel):
    """v1: deterministic skill-overlap scoring, no LLM call. Transparent
    and free to run on every vacancy - `matched_skills` IS the reasoning,
    not a summary of one. A semantic layer (synonyms, seniority fit,
    "why this vacancy" prose) is a natural v2, but needs an LLM call and
    an API key we haven't wired up yet."""

    vacancy_title: str
    vacancy_company: str
    vacancy_url: str
    score: int
    matched_skills: list[str]


def score_vacancy(vacancy: Vacancy, profile_skills: list[str]) -> MatchResult:
    haystack = f"{vacancy.title} {vacancy.description} {' '.join(vacancy.tags)}".lower()
    matched = [skill for skill in profile_skills if skill.lower() in haystack]
    score = min(100, len(matched) * POINTS_PER_MATCHED_SKILL)

    return MatchResult(
        vacancy_title=vacancy.title,
        vacancy_company=vacancy.company,
        vacancy_url=vacancy.url,
        score=score,
        matched_skills=matched,
    )


def score_vacancies(vacancies: list[Vacancy], profile_skills: list[str]) -> list[MatchResult]:
    results = [score_vacancy(v, profile_skills) for v in vacancies]
    return sorted(results, key=lambda r: r.score, reverse=True)

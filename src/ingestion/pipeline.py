from .models import Vacancy
from .sources.base import VacancySource


def fetch_all(sources: list[VacancySource], keywords: list[str], location: str) -> list[Vacancy]:
    """Fan out to every configured source and dedupe by (source, external_id).
    A vacancy that reappears across ingestion runs shouldn't be treated as new."""
    seen: set[tuple[str, str]] = set()
    vacancies: list[Vacancy] = []

    for source in sources:
        for vacancy in source.fetch(keywords, location):
            key = (vacancy.source, vacancy.external_id)
            if key in seen:
                continue
            seen.add(key)
            vacancies.append(vacancy)

    return vacancies

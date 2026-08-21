from djinni_scraper import DjinniScraper
from djinni_scraper import Job as DjinniJob

from ..models import Vacancy
from .base import VacancySource


class DjinniSource(VacancySource):
    """Thin adapter around the standalone djinni-scraper library - maps
    its Job model onto career-copilot's internal Vacancy DTO. All the
    actual scraping logic (listing pagination, JSON-LD parsing, no-
    /my/dashboard/ stance) lives in that library now; see its README for
    what was learned building it.

    Unlike Arbeitnow/DOU, description needs no HTML stripping - Djinni's
    JobPosting JSON-LD already carries clean plain text (confirmed live
    across every postings checked while building this)."""

    name = "djinni"

    def __init__(self, **client_kwargs):
        self._client = DjinniScraper(**client_kwargs)

    def fetch(self, keywords: list[str], location: str) -> list[Vacancy]:
        return [self._to_vacancy(job) for job in self._client.search(keywords, location)]

    @staticmethod
    def _to_vacancy(job: DjinniJob) -> Vacancy:
        return Vacancy(
            source="djinni",
            external_id=job.external_id,
            title=job.title,
            company=job.company,
            location=job.location,
            remote=job.remote,
            url=job.url,
            description=job.description,
            tags=job.tags,
            created_at=job.created_at,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
        )

import os

from adzuna_client import AdzunaClient
from adzuna_client import Job as AdzunaJob

from ..models import Vacancy
from .base import VacancySource


class AdzunaSource(VacancySource):
    """Thin adapter around the standalone adzuna-client library - maps
    its Job model onto career-copilot's internal Vacancy DTO. All the
    actual API logic (retry, pagination, salary parsing) lives in that
    library now; see its README for what was learned building it."""

    name = "adzuna"

    def __init__(self, **client_kwargs):
        self._client = AdzunaClient(
            app_id=os.environ["ADZUNA_APP_ID"],
            app_key=os.environ["ADZUNA_APP_KEY"],
            **client_kwargs,
        )

    def fetch(self, keywords: list[str], location: str) -> list[Vacancy]:
        return [self._to_vacancy(job) for job in self._client.search(keywords, location)]

    @staticmethod
    def _to_vacancy(job: AdzunaJob) -> Vacancy:
        return Vacancy(
            source="adzuna",
            external_id=job.external_id,
            title=job.title,
            company=job.company,
            location=job.location,
            remote=False,
            url=job.url,
            description=job.description,
            tags=[job.category] if job.category else [],
            created_at=job.created_at,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            salary_period=job.salary_period,
            salary_is_predicted=job.salary_is_predicted,
        )

from datetime import datetime

from arbeitnow_client import ArbeitnowClient
from arbeitnow_client import Job as ArbeitnowJob

from ..models import Vacancy
from .base import VacancySource


class ArbeitnowSource(VacancySource):
    """Thin adapter around the standalone arbeitnow-client library - maps
    its Job model onto career-copilot's internal Vacancy DTO. All the
    actual API logic (retry, pagination, keyword filtering) lives in that
    library now; see its README for what was learned building it."""

    name = "arbeitnow"

    def __init__(self, **client_kwargs):
        self._client = ArbeitnowClient(**client_kwargs)

    def fetch(self, keywords: list[str], location: str) -> list[Vacancy]:
        return [self._to_vacancy(job) for job in self._client.search(keywords, location)]

    @staticmethod
    def _to_vacancy(job: ArbeitnowJob) -> Vacancy:
        return Vacancy(
            source="arbeitnow",
            external_id=job.external_id,
            title=job.title,
            company=job.company,
            location=job.location,
            remote=job.remote,
            url=job.url,
            description=job.description,
            tags=job.tags,
            created_at=datetime.fromtimestamp(job.created_at) if job.created_at else None,
        )

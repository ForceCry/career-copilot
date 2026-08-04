import os
import time

import httpx

from ..http import get_with_retry
from ..models import Vacancy
from .base import VacancySource

API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class AdzunaSource(VacancySource):
    """Requires a free app_id/app_key from https://developer.adzuna.com/.
    Unlike Arbeitnow, `what` (keywords) and `where` (location) are real
    server-side filters, and `pl` (Poland) is a supported country code.

    No documented rate limit was found for the public API, but request_delay
    + get_with_retry are here anyway - being a well-behaved client shouldn't
    depend on whether a provider happens to publish a limit."""

    name = "adzuna"

    def __init__(
        self,
        country: str = "pl",
        results_per_page: int = 50,
        max_pages: int = 3,
        timeout: float = 15.0,
        request_delay: float = 0.3,
    ):
        self.app_id = os.environ["ADZUNA_APP_ID"]
        self.app_key = os.environ["ADZUNA_APP_KEY"]
        self.country = country
        self.results_per_page = results_per_page
        self.max_pages = max_pages
        self.timeout = timeout
        self.request_delay = request_delay

    def fetch(self, keywords: list[str], location: str) -> list[Vacancy]:
        results: list[Vacancy] = []

        with httpx.Client(timeout=self.timeout) as client:
            for page in range(1, self.max_pages + 1):
                if page > 1:
                    time.sleep(self.request_delay)

                url = API_URL.format(country=self.country, page=page)
                params = {
                    "app_id": self.app_id,
                    "app_key": self.app_key,
                    "results_per_page": self.results_per_page,
                    "what": " ".join(keywords),
                    "where": location,
                    "content-type": "application/json",
                }
                response = get_with_retry(client, url, params=params)
                response.raise_for_status()
                jobs = response.json().get("results", [])
                if not jobs:
                    break
                results.extend(self._to_vacancy(job) for job in jobs)

        return results

    @staticmethod
    def _to_vacancy(job: dict) -> Vacancy:
        return Vacancy(
            source="adzuna",
            external_id=str(job["id"]),
            title=job.get("title", ""),
            company=job.get("company", {}).get("display_name", ""),
            location=job.get("location", {}).get("display_name", ""),
            remote=False,
            url=job.get("redirect_url", ""),
            description=job.get("description", ""),
            tags=[job.get("category", {}).get("label", "")],
            created_at=job.get("created"),
        )

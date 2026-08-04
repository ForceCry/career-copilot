import time

import httpx

from ..http import get_with_retry
from ..models import Vacancy
from .base import VacancySource

API_URL = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowSource(VacancySource):
    """No API key required, but the API ignores search/tag query params —
    confirmed by hand against the live endpoint. Filtering happens
    client-side, and volume for PHP/Symfony is low (~1 in 175 per page),
    so treat this as a supplementary source, not the primary one.

    Rate limiting is real here, not theoretical: a handful of quick
    requests in a row triggered a 429 behind a Cloudflare challenge during
    testing. request_delay + get_with_retry exist because of that, not as
    defensive boilerplate.
    """

    name = "arbeitnow"

    def __init__(self, max_pages: int = 3, timeout: float = 15.0, request_delay: float = 1.0):
        self.max_pages = max_pages
        self.timeout = timeout
        self.request_delay = request_delay

    def fetch(self, keywords: list[str], location: str) -> list[Vacancy]:
        keywords_lower = [k.lower() for k in keywords]
        results: list[Vacancy] = []

        with httpx.Client(timeout=self.timeout) as client:
            url = API_URL
            for page_num in range(self.max_pages):
                if not url:
                    break
                if page_num > 0:
                    time.sleep(self.request_delay)

                response = get_with_retry(client, url)
                response.raise_for_status()
                payload = response.json()

                for job in payload["data"]:
                    haystack = " ".join(
                        [job["title"], job["description"], " ".join(job.get("tags", []))]
                    ).lower()
                    if any(k in haystack for k in keywords_lower):
                        results.append(self._to_vacancy(job))

                url = payload.get("links", {}).get("next")

        return results

    @staticmethod
    def _to_vacancy(job: dict) -> Vacancy:
        return Vacancy(
            source="arbeitnow",
            external_id=job["slug"],
            title=job["title"],
            company=job["company_name"],
            location=job.get("location", ""),
            remote=bool(job.get("remote")),
            url=job["url"],
            description=job.get("description", ""),
            tags=job.get("tags", []),
        )

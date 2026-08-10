import time
from urllib.parse import urlparse

import httpx

from .models import Job
from .retry import get_with_retry

API_URL = "https://www.arbeitnow.com/api/job-board-api"
ALLOWED_HOST = "www.arbeitnow.com"


class ArbeitnowClient:
    """Client for https://www.arbeitnow.com/api/job-board-api - no API key
    needed, but confirmed live: the API's `search`/`tags` query params are
    accepted but ignored server-side, so keyword filtering here happens
    client-side after fetching each page. PHP/Symfony volume in
    particular is low (~1 in 175 per page tested), so this is a
    supplementary source for a PHP-focused search, not a primary one.

    No salary data - confirmed live, job objects simply don't include a
    salary field.

    Rate limiting is real, not theoretical: a handful of quick requests
    in a row triggered a 429 behind a Cloudflare challenge during
    testing. request_delay + get_with_retry exist because of that.
    """

    def __init__(self, max_pages: int = 3, timeout: float = 15.0, request_delay: float = 1.0):
        self.max_pages = max_pages
        self.timeout = timeout
        self.request_delay = request_delay

    def search(self, keywords: list[str], location: str) -> list[Job]:
        """`location` is accepted for interface consistency with other
        job clients but not applied - Arbeitnow's API has no server-side
        location filter, and this client doesn't fake one client-side to
        avoid pretending a filter works when it doesn't."""
        keywords_lower = [k.lower() for k in keywords]
        results: list[Job] = []

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
                        results.append(self._to_job(job))

                url = self._validate_next_url(payload.get("links", {}).get("next"))

        return results

    @staticmethod
    def _validate_next_url(url: str | None) -> str | None:
        # links.next comes straight from the API response and was
        # followed unconditionally - a compromised or malformed response
        # could redirect this client to an arbitrary host, including
        # internal network endpoints reachable from wherever this runs
        # (SSRF). Flagged by an independent Codex review. Only ever
        # follow it if it still points at the same host over https.
        if not url:
            return None
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
            return None
        return url

    @staticmethod
    def _to_job(job: dict) -> Job:
        return Job(
            external_id=job["slug"],
            title=job["title"],
            company=job["company_name"],
            location=job.get("location", ""),
            remote=bool(job.get("remote")),
            url=job["url"],
            description=job.get("description", ""),
            tags=job.get("tags", []),
            created_at=job.get("created_at"),
        )

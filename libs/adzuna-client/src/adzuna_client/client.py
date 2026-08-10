import time

import httpx

from .errors import AdzunaAPIError
from .models import Job
from .retry import get_with_retry

API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"

# The API doesn't return a currency field - it's implied by the country
# endpoint. Covers every country Adzuna publishes an endpoint for.
COUNTRY_CURRENCY = {
    "at": "EUR", "au": "AUD", "br": "BRL", "ca": "CAD", "de": "EUR",
    "fr": "EUR", "gb": "GBP", "in": "INR", "it": "EUR", "mx": "MXN",
    "nl": "EUR", "nz": "NZD", "pl": "PLN", "sg": "SGD", "us": "USD",
    "za": "ZAR", "es": "EUR", "se": "SEK", "ch": "CHF",
}


class AdzunaClient:
    """Client for https://developer.adzuna.com/ - requires a free
    app_id/app_key. `what` (keywords) and `where` (location) are real
    server-side filters, and country codes like `pl` (Poland) select
    which of Adzuna's per-country endpoints to query.

    No documented rate limit was found for the public API, but
    request_delay + get_with_retry are here anyway - being a well-behaved
    client shouldn't depend on whether a provider happens to publish one.
    """

    def __init__(
        self,
        app_id: str,
        app_key: str,
        country: str = "pl",
        results_per_page: int = 50,
        max_pages: int = 3,
        timeout: float = 15.0,
        request_delay: float = 0.3,
    ):
        self.app_id = app_id
        self.app_key = app_key
        self.country = country
        self.results_per_page = results_per_page
        self.max_pages = max_pages
        self.timeout = timeout
        self.request_delay = request_delay

    def search(self, keywords: list[str], location: str) -> list[Job]:
        results: list[Job] = []

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
                self._raise_for_status(response)
                jobs = response.json().get("results", [])
                if not jobs:
                    break
                results.extend(self._to_job(job) for job in jobs)

        return results

    def _raise_for_status(self, response: httpx.Response) -> None:
        # Two things had to be true for this to actually redact the
        # credential, not just the exception's own message text: (1)
        # rebuild the URL from its parsed query params rather than a
        # naive string .replace(), which misses values httpx percent-
        # encoded; (2) raise a plain exception that never holds a
        # reference to the original response/request objects - those
        # carry the unredacted URL internally regardless of what message
        # string gets attached, and str.replace() on the message alone
        # (the previous fix) didn't stop e.request.url from leaking it.
        # Confirmed by an independent Codex review, not the original design.
        if not response.is_error:
            return
        params = {**dict(response.url.params), "app_id": "***", "app_key": "***"}
        safe_url = str(response.url.copy_with(params=params))
        raise AdzunaAPIError(
            response.status_code, f"{response.status_code} error response for url '{safe_url}'"
        )

    def _to_job(self, job: dict) -> Job:
        return Job(
            external_id=str(job["id"]),
            title=job.get("title", ""),
            company=job.get("company", {}).get("display_name", ""),
            location=job.get("location", {}).get("display_name", ""),
            url=job.get("redirect_url", ""),
            description=job.get("description", ""),
            category=job.get("category", {}).get("label", ""),
            created_at=job.get("created"),
            salary_min=job.get("salary_min"),
            salary_max=job.get("salary_max"),
            salary_currency=COUNTRY_CURRENCY.get(self.country, ""),
            salary_period="year",
            salary_is_predicted=str(job.get("salary_is_predicted")) == "1",
        )

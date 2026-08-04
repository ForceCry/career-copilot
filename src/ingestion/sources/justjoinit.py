import json
import re
import time
from xml.etree import ElementTree

import httpx

from ..http import get_with_retry
from ..models import Vacancy
from .base import VacancySource

SITEMAP_INDEX_URL = "https://justjoin.it/sitemaps/active-jobs.xml"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
JSON_LD_PATTERN = re.compile(
    r'<script type="application/ld\+json">(.*?)</script>', re.DOTALL
)


class JustJoinItSource(VacancySource):
    """justjoin.it has no public partner API — api.justjoin.it is their
    private frontend backend, and robots.txt explicitly disallows /api/.
    What they DO publish deliberately is a sitemap of active job postings
    (robots.txt -> Sitemap: .../active-jobs.xml), and each job page embeds
    a schema.org JobPosting JSON-LD block meant for search engines. This
    source reads only those two sanctioned surfaces: sitemap for the URL
    list, JSON-LD for the structured data. No private endpoints touched.

    Job pages are fetched one at a time with a delay between requests —
    politeness, not just rate-limit avoidance.
    """

    name = "justjoinit"

    def __init__(self, request_delay: float = 0.5, timeout: float = 15.0):
        self.request_delay = request_delay
        self.timeout = timeout

    def fetch(self, keywords: list[str], location: str) -> list[Vacancy]:
        keywords_lower = [k.lower() for k in keywords]

        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            job_urls = self._collect_job_urls(client)
            matching_urls = [
                url for url in job_urls if any(k in url.lower() for k in keywords_lower)
            ]

            vacancies: list[Vacancy] = []
            for url in matching_urls:
                job_posting = self._fetch_job_posting(client, url)
                if job_posting:
                    vacancies.append(self._to_vacancy(url, job_posting))
                time.sleep(self.request_delay)

        return vacancies

    def _collect_job_urls(self, client: httpx.Client) -> list[str]:
        index_response = get_with_retry(client, SITEMAP_INDEX_URL)
        index_response.raise_for_status()
        index_root = ElementTree.fromstring(index_response.content)
        part_urls = [
            el.text for el in index_root.iter(f"{SITEMAP_NS}loc") if el.text
        ]

        urls: list[str] = []
        for part_url in part_urls:
            part_response = get_with_retry(client, part_url)
            part_response.raise_for_status()
            part_root = ElementTree.fromstring(part_response.content)
            urls.extend(
                el.text for el in part_root.iter(f"{SITEMAP_NS}loc") if el.text
            )
        return urls

    def _fetch_job_posting(self, client: httpx.Client, url: str) -> dict | None:
        response = get_with_retry(client, url)
        if response.status_code != 200:
            return None
        match = JSON_LD_PATTERN.search(response.text)
        if not match:
            return None
        return json.loads(match.group(1))

    @staticmethod
    def _to_vacancy(url: str, job_posting: dict) -> Vacancy:
        address = job_posting.get("jobLocation", {}).get("address", {})
        location_parts = [address.get("addressLocality", ""), address.get("addressCountry", "")]
        location = ", ".join(p for p in location_parts if p)

        base_salary = job_posting.get("baseSalary", {})
        salary_value = base_salary.get("value", {})
        salary_period = {"MONTH": "month", "YEAR": "year", "HOUR": "hour"}.get(
            salary_value.get("unitText", ""), ""
        )

        return Vacancy(
            source="justjoinit",
            external_id=url.rstrip("/").rsplit("/", 1)[-1],
            title=job_posting.get("title", ""),
            company=job_posting.get("hiringOrganization", {}).get("name", ""),
            location=location,
            remote=job_posting.get("jobLocationType") == "TELECOMMUTE",
            url=url,
            description=job_posting.get("description", ""),
            tags=[],
            created_at=job_posting.get("datePosted"),
            salary_min=salary_value.get("minValue"),
            salary_max=salary_value.get("maxValue"),
            salary_currency=base_salary.get("currency", ""),
            salary_period=salary_period,
        )

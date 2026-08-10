import json
import re
import time
from xml.etree import ElementTree

import httpx
from pydantic import ValidationError

from .models import Job
from .retry import get_with_retry

SITEMAP_INDEX_URL = "https://justjoin.it/sitemaps/active-jobs.xml"
SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
# Matches either quote style and any attribute order/extras on the script
# tag (e.g. type="application/ld+json" id="x") - the original pattern
# only matched one exact serialization and would silently stop finding
# anything after a harmless markup change. Finds every ld+json block on
# the page, not just the first: real pages can carry more than one
# (breadcrumbs, org info), and picking blindly risked converting an
# unrelated object into a mostly-empty Job.
JSON_LD_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL
)


class JustJoinItScraper:
    """justjoin.it has no public partner API - api.justjoin.it is their
    private frontend backend, and their own robots.txt explicitly
    disallows /api/. What they DO publish deliberately is a sitemap of
    active job postings (robots.txt -> Sitemap: .../active-jobs.xml), and
    each job page embeds a schema.org JobPosting JSON-LD block meant for
    search engines. This reads only those two sanctioned surfaces -
    sitemap for the URL list, JSON-LD for the structured data. No private
    endpoints touched, which is why this is named a "scraper" and not a
    "client": it's not talking to an API meant for this.

    Slower than a real API by nature of this approach: one full page
    fetch per matching posting, since there's no bulk search endpoint.
    request_delay paces those fetches - politeness, not just self-
    protection against rate limits.
    """

    def __init__(self, request_delay: float = 0.5, timeout: float = 15.0):
        self.request_delay = request_delay
        self.timeout = timeout

    def search(self, keywords: list[str], location: str) -> list[Job]:
        """`location` is accepted for interface consistency with other
        job clients but not applied - matching happens against the URL
        slug only, which encodes role/tech but not reliably location."""
        keywords_lower = [k.lower() for k in keywords]

        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            job_urls = self._collect_job_urls(client)
            matching_urls = [
                url for url in job_urls if any(k in url.lower() for k in keywords_lower)
            ]

            jobs: list[Job] = []
            for url in matching_urls:
                try:
                    job_posting = self._fetch_job_posting(client, url)
                    if job_posting:
                        jobs.append(self._to_job(url, job_posting))
                except (
                    json.JSONDecodeError, AttributeError, TypeError, KeyError,
                    ValidationError, httpx.HTTPError,
                ):
                    # One malformed/unexpected posting shouldn't abort the
                    # whole batch and lose every posting already fetched.
                    # ValidationError and httpx.HTTPError were added after
                    # an independent Codex re-review found the original set
                    # didn't cover them: a JSON-LD block that parses fine
                    # but fails Job's own field validation (e.g. an
                    # unparseable datePosted), or a job page that keeps
                    # failing to fetch even after get_with_retry's retries
                    # are exhausted, both still aborted the whole search.
                    pass
                time.sleep(self.request_delay)

        return jobs

    def _collect_job_urls(self, client: httpx.Client) -> list[str]:
        index_response = get_with_retry(client, SITEMAP_INDEX_URL)
        index_response.raise_for_status()
        index_root = ElementTree.fromstring(index_response.content)
        part_urls = [el.text for el in index_root.iter(f"{SITEMAP_NS}loc") if el.text]

        urls: list[str] = []
        for part_url in part_urls:
            part_response = get_with_retry(client, part_url)
            part_response.raise_for_status()
            part_root = ElementTree.fromstring(part_response.content)
            urls.extend(el.text for el in part_root.iter(f"{SITEMAP_NS}loc") if el.text)
        return urls

    def _fetch_job_posting(self, client: httpx.Client, url: str) -> dict | None:
        response = get_with_retry(client, url)
        if response.status_code != 200:
            return None

        candidates = [json.loads(m) for m in JSON_LD_PATTERN.findall(response.text)]
        if not candidates:
            return None
        # Prefer the block that's actually a JobPosting - a page with
        # multiple JSON-LD blocks (breadcrumbs, org info) previously used
        # whichever one the regex matched first, regardless of @type.
        for candidate in candidates:
            if candidate.get("@type") == "JobPosting":
                return candidate
        return candidates[0]

    @staticmethod
    def _to_job(url: str, job_posting: dict) -> Job:
        # `.get(key, {})` only substitutes the default when the key is
        # *missing* - real JobPosting JSON-LD from the live site has
        # baseSalary present but explicitly `null` for unpaid/unlisted
        # postings, which made `.get("value", {})` on None raise
        # AttributeError. `or {}` covers both missing and explicit-null.
        # Confirmed against a real live posting during an independent
        # Codex review, not assumed.
        address = (job_posting.get("jobLocation") or {}).get("address") or {}
        location_parts = [address.get("addressLocality", ""), address.get("addressCountry", "")]
        location = ", ".join(p for p in location_parts if p)

        base_salary = job_posting.get("baseSalary") or {}
        salary_value = base_salary.get("value") or {}
        salary_period = {"MONTH": "month", "YEAR": "year", "HOUR": "hour"}.get(
            salary_value.get("unitText", ""), ""
        )

        return Job(
            external_id=url.rstrip("/").rsplit("/", 1)[-1],
            title=job_posting.get("title", ""),
            company=(job_posting.get("hiringOrganization") or {}).get("name", ""),
            location=location,
            remote=job_posting.get("jobLocationType") == "TELECOMMUTE",
            url=url,
            description=job_posting.get("description", ""),
            created_at=job_posting.get("datePosted"),
            salary_min=salary_value.get("minValue"),
            salary_max=salary_value.get("maxValue"),
            salary_currency=base_salary.get("currency", ""),
            salary_period=salary_period,
        )

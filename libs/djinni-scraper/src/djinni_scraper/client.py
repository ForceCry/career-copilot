import json
import re
import time

import httpx
from pydantic import ValidationError

from .models import Job
from .retry import get_with_retry

LISTING_URL = "https://djinni.co/jobs/"
BASE_URL = "https://djinni.co"

# Matches either quote style and any attribute order/extras on the script
# tag - same pattern justjoinit-scraper already uses for the same reason
# (a schema.org JobPosting block meant for Google Jobs indexing, not a
# private API). Finds every ld+json block on a page, not just the first:
# a real page can carry more than one (breadcrumbs, org info).
JSON_LD_PATTERN = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL
)

# Real link shape confirmed live: href="/jobs/358246-php-laravel-developer/"
# (numeric id, then a slug - the slug can itself end in a trailing hyphen
# on a real posting, e.g. ".../380777-php-developer-laravel-/", so this
# doesn't try to validate the slug's own shape, only capture the full path).
_JOB_LINK_RE = re.compile(r'href="(/jobs/\d+-[^"]*/)"')
# Same numeric-id prefix _JOB_LINK_RE already relies on for every URL this
# scraper ever visits - used to derive external_id directly from the URL
# rather than the JSON-LD's own `identifier` field (see _to_job's docstring
# for why).
_URL_ID_RE = re.compile(r"/jobs/(\d+)-")


class DjinniScraper:
    """djinni.co has no partner API - this reads two surfaces it serves
    publicly and without login: the search listing (only to discover
    posting URLs) and each posting's own schema.org JobPosting JSON-LD
    block. See this package's README for why NOT scraping
    /my/dashboard/ (a logged-in user's own personalized recommendations,
    not public data) and other known limitations.

    `primary_keyword` is Djinni's own search parameter (e.g. "PHP" -
    see the listing page's own search filters), not a free-text
    multi-keyword search - there's no way to combine several keywords
    server-side. Defaults to "PHP" since that's this project's actual
    focus.

    Slower than a real API by nature of this approach: one full page
    fetch per posting (no bulk detail endpoint), on top of the listing
    pages needed just to discover the URLs. request_delay paces every
    fetch - politeness, not just self-protection against rate limits.

    max_pages caps how many postings a single search() can ever return
    (max_pages * ~15 per page) - confirmed live the real PHP category
    currently totals ~36 postings (well under the default's ~75-slot
    ceiling), but this is a real, silent truncation risk if that grows:
    a still-open posting that never makes page 1-5 would look identical,
    to storage/vacancy_repo.mark_missing_vacancies, to one that's
    actually gone - flagged by an independent Codex review. Widening
    this is a mitigation, not a fix - there's no bulk endpoint to make
    "always see every posting in one run" actually guaranteed.
    """

    def __init__(
        self,
        primary_keyword: str = "PHP",
        max_pages: int = 5,
        timeout: float = 15.0,
        request_delay: float = 0.5,
    ):
        self.primary_keyword = primary_keyword
        self.max_pages = max_pages
        self.timeout = timeout
        self.request_delay = request_delay

    def search(self, keywords: list[str], location: str) -> list[Job]:
        """`keywords`/`location` accepted for interface consistency with
        the other job clients in this project, but not applied - see
        this class's docstring on `primary_keyword`."""
        with httpx.Client(
            timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
        ) as client:
            urls = self._collect_job_urls(client)

            jobs: list[Job] = []
            for url in urls:
                try:
                    job_posting = self._fetch_job_posting(client, url)
                    if job_posting:
                        jobs.append(self._to_job(url, job_posting))
                except (
                    json.JSONDecodeError, AttributeError, TypeError, KeyError,
                    ValidationError, httpx.HTTPError,
                ):
                    # One malformed/unexpected posting shouldn't abort the
                    # whole batch and lose every posting already fetched -
                    # same defensive shape justjoinit-scraper already
                    # uses for the identical class of risk (a JSON-LD
                    # block that parses but fails Job's own validation,
                    # or a page that keeps failing to fetch even after
                    # get_with_retry's retries are exhausted).
                    pass
                time.sleep(self.request_delay)

        return jobs

    def _collect_job_urls(self, client: httpx.Client) -> list[str]:
        seen: dict[str, None] = {}  # insertion-ordered dedup - a posting
        # could in principle appear on more than one page if the listing
        # shifts between fetches (a new posting pushing others down a page).
        for page in range(1, self.max_pages + 1):
            if page > 1:
                time.sleep(self.request_delay)

            params = {"primary_keyword": self.primary_keyword, "page": page}
            response = get_with_retry(client, LISTING_URL, params=params)
            if response.status_code != 200:
                break
            if response.history:
                # A page number past the real end of results 302-redirects
                # to the UNFILTERED /jobs/ listing (not an empty page) -
                # confirmed live by an independent Codex review: with
                # follow_redirects=True (needed for detail-page fetches),
                # that redirect's destination still returns a real, full
                # page of job links, just for every category, not
                # primary_keyword. Treating that as "more real results"
                # silently pulled in unrelated postings (CRM Manager,
                # Operations Manager, .NET roles) mislabeled as this
                # scraper's configured keyword. Any redirect during listing
                # pagination means "this page doesn't exist" - stop, and
                # discard whatever links are on the redirect's target.
                break

            found = _JOB_LINK_RE.findall(response.text)
            if not found:
                break

            for path in found:
                seen.setdefault(BASE_URL + path, None)

        return list(seen)

    def _fetch_job_posting(self, client: httpx.Client, url: str) -> dict | None:
        response = get_with_retry(client, url)
        if response.status_code != 200:
            return None

        candidates = [json.loads(m) for m in JSON_LD_PATTERN.findall(response.text)]
        if not candidates:
            return None
        for candidate in candidates:
            if candidate.get("@type") == "JobPosting":
                return candidate
        return None

    @staticmethod
    def _to_job(url: str, job_posting: dict) -> Job:
        # `.get(key, {})` only substitutes the default when the key is
        # *missing*, not when it's explicitly `null` - real JobPosting
        # JSON-LD can (and does, confirmed live) have baseSalary present
        # but explicitly null for a posting that didn't disclose one.
        # `or {}` covers both missing and explicit-null - same fix an
        # independent Codex review already made for justjoinit-scraper's
        # identical parsing shape, applied here up front rather than
        # waiting to rediscover it.
        address = (job_posting.get("jobLocation") or {}).get("address") or {}
        locality = address.get("addressLocality")
        location_parts = [
            ", ".join(locality) if isinstance(locality, list) else locality or "",
            address.get("addressCountry") or "",
        ]
        location = ", ".join(p for p in location_parts if p)

        base_salary = job_posting.get("baseSalary") or {}
        salary_value = base_salary.get("value") or {}
        salary_period = {"MONTH": "month", "YEAR": "year", "HOUR": "hour"}.get(
            salary_value.get("unitText", ""), ""
        )

        # Deliberately NOT using the JSON-LD's own `identifier` field -
        # flagged by an independent Codex review: schema.org allows it to
        # be a PropertyValue object rather than a scalar (str(identifier)
        # would then persist the dict's repr), and its presence isn't
        # guaranteed stable across two different fetches of the same
        # posting - either would risk a duplicate DB row for one real
        # posting (upsert_vacancies keys off (source, external_id)).
        # The numeric id in the URL, by contrast, is what discovery
        # already required to reach this posting at all (_JOB_LINK_RE
        # only matches links with that prefix) - always present, always
        # the same value for the same real posting.
        final_url = job_posting.get("url") or url
        id_match = _URL_ID_RE.search(final_url) or _URL_ID_RE.search(url)

        return Job(
            external_id=id_match.group(1) if id_match else final_url.rstrip("/").rsplit("/", 1)[-1],
            title=job_posting.get("title", ""),
            company=(job_posting.get("hiringOrganization") or {}).get("name", ""),
            location=location,
            remote=job_posting.get("jobLocationType") == "TELECOMMUTE",
            url=final_url,
            description=job_posting.get("description", ""),
            created_at=job_posting.get("datePosted"),
            salary_min=salary_value.get("minValue"),
            salary_max=salary_value.get("maxValue"),
            salary_currency=base_salary.get("currency", ""),
            salary_period=salary_period,
        )

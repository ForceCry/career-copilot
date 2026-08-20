import re
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree

import httpx

from .models import Job
from .retry import get_with_retry

FEED_URL = "https://jobs.dou.ua/vacancies/feeds/"

# DOU's own feed template, confirmed live against real titles:
# "{title} в {company}[, ${salary}][, {city1}[, {city2}...]][, за кордоном]
# [, віддалено]" - e.g. "Senior PHP Developer в FAVBET, Київ, за кордоном,
# віддалено". Split on the LAST standalone " в ", not the first - flagged
# by an independent Codex review and confirmed live: a real posting's own
# title can itself contain " в " ("Treasury Specialist / Financial
# Specialist в Glovo" is the actual job title, per DOU's own vacancy page
# <h1>), and DOU's template appends its OWN " в {company}..." after that
# unconditionally, producing a feed title like "...в Glovo в Glovo,
# Київ". The real, template-inserted separator is always the LAST one -
# anything earlier is part of the title itself, not the boundary.
_TITLE_SPLIT_RE = re.compile(r"\s+в\s+")
_REMOTE_MARKER = "віддалено"
_VACANCY_ID_RE = re.compile(r"/vacancies/(\d+)/")

# Handles every salary shape confirmed live across DOU's RSS (checked
# ~1,150 titles across 59 categories, flagged by an independent Codex
# review after the initial version only handled the range form):
# "$2500–2700" (range, en dash or hyphen), "$700" (exact), "від $1800"
# ("from", lower bound only), "до $2500" ("up to", upper bound only). The
# "від"/"до" forms use \s+ rather than a literal space because DOU
# renders a non-breaking space (&nbsp;, U+00A0) there, which \s already
# matches - confirmed live, not an assumption.
_SALARY_RANGE_RE = re.compile(r"^\$([\d,]+)[–-]([\d,]+)$")
_SALARY_FROM_RE = re.compile(r"^від\s+\$([\d,]+)$")
_SALARY_TO_RE = re.compile(r"^до\s+\$([\d,]+)$")
_SALARY_EXACT_RE = re.compile(r"^\$([\d,]+)$")


def _parse_salary(segment: str) -> tuple[float | None, float | None] | None:
    """Returns (min, max) if `segment` is recognizably a salary figure,
    None otherwise (so the caller falls through to treating it as
    location text). max is None for an open-ended "від" ("from") lower
    bound - never assumed equal to min, since that would misrepresent an
    open range as an exact figure. A reversed range ("$3000–1000") is
    swapped rather than stored backwards - DOU doesn't validate this
    server-side, confirmed a handful of real postings have it."""
    range_match = _SALARY_RANGE_RE.match(segment)
    if range_match:
        low = float(range_match.group(1).replace(",", ""))
        high = float(range_match.group(2).replace(",", ""))
        return (low, high) if low <= high else (high, low)

    from_match = _SALARY_FROM_RE.match(segment)
    if from_match:
        return float(from_match.group(1).replace(",", "")), None

    to_match = _SALARY_TO_RE.match(segment)
    if to_match:
        return None, float(to_match.group(1).replace(",", ""))

    exact_match = _SALARY_EXACT_RE.match(segment)
    if exact_match:
        value = float(exact_match.group(1).replace(",", ""))
        return value, value

    return None


def _parse_title(raw_title: str) -> tuple[str, str, str, bool, float | None, float | None, str]:
    """Returns (title, company, location, remote, salary_min, salary_max,
    salary_currency). DOU doesn't expose these as separate RSS fields -
    they're all packed into one free-text title string DOU renders
    itself (see module docstring for the template). Best-effort: an
    unparseable trailing segment - "за кордоном" ("abroad"), or anything
    that isn't recognizably a salary or the remote marker - is folded
    into `location` rather than dropped, so a segment this doesn't have
    a dedicated Vacancy field for is still visible somewhere rather than
    silently discarded. A missing " в " separator entirely just returns
    the raw title with an empty company/location - never raises.

    A company name that itself contains a comma (confirmed real: "SIS,
    LLC (Ukraine)") is a known, accepted gap - splitting the tail on ","
    has no way to distinguish that from a real field boundary without a
    company-name allowlist. Left as-is deliberately: `company`/
    `location` are display metadata only, never part of the text actually
    embedded for matching (see search.indexer.build_vacancy_embedding_text
    - only title+description feed that), so the practical cost of this
    gap is cosmetic, not a matching-quality regression."""
    parts = _TITLE_SPLIT_RE.split(raw_title)
    if len(parts) < 2:
        return raw_title.strip(), "", "", False, None, None, ""

    title, tail = " в ".join(parts[:-1]).strip(), parts[-1]
    segments = [s.strip() for s in tail.split(",") if s.strip()]
    if not segments:
        return title, "", "", False, None, None, ""

    company = segments[0]
    remote = False
    salary_min = salary_max = None
    salary_currency = ""
    location_parts = []

    for segment in segments[1:]:
        if segment == _REMOTE_MARKER:
            remote = True
            continue
        salary = _parse_salary(segment)
        if salary is not None:
            salary_min, salary_max = salary
            salary_currency = "USD"
            continue
        # Catches both "за кордоном" and any city name(s) - neither gets
        # special-cased beyond the two markers above, they're both just
        # free-text location context.
        location_parts.append(segment)

    return title, company, ", ".join(location_parts), remote, salary_min, salary_max, salary_currency


class DouClient:
    """Reads DOU.ua's public vacancies RSS feed - see this package's
    README for why RSS over scraping (DOU explicitly publishes it, linked
    from every category page) and its known limitations (only the ~25
    latest postings per fetch, confirmed live that neither `count` nor
    `page` query params change that).

    `category` is DOU's own fixed taxonomy tag (e.g. "PHP", "Python",
    "Java" - see the category filter on https://jobs.dou.ua/vacancies/),
    not a free-text keyword search like Adzuna's `what` - there's no way
    to combine multiple keywords server-side. Defaults to "PHP" since
    that's this project's actual focus."""

    def __init__(self, category: str = "PHP", timeout: float = 15.0):
        self.category = category
        self.timeout = timeout

    def search(self, keywords: list[str], location: str) -> list[Job]:
        """`keywords`/`location` accepted for interface consistency with
        the other job clients in this project, but not applied - the
        `category` passed to __init__ is the only filter this feed
        supports, and it's already targeted (unlike Arbeitnow, whose API
        ignores search params entirely and needs client-side keyword
        filtering to be usable at all) - adding a client-side re-filter
        on top of it would just as easily throw away a real PHP posting
        that doesn't happen to also repeat "symfony" or "backend"
        verbatim as it would filter noise."""
        with httpx.Client(timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0"}) as client:
            response = get_with_retry(client, FEED_URL, params={"category": self.category})
            response.raise_for_status()
            root = ElementTree.fromstring(response.content)

        jobs = []
        for item in root.findall(".//item"):
            job = self._to_job(item)
            if job:
                jobs.append(job)
        return jobs

    @staticmethod
    def _to_job(item: ElementTree.Element) -> Job | None:
        link_el = item.find("link")
        title_el = item.find("title")
        if link_el is None or title_el is None or not link_el.text or not title_el.text:
            return None

        url = link_el.text.split("?")[0]
        id_match = _VACANCY_ID_RE.search(url)
        if not id_match:
            return None

        # ElementTree's own XML parsing already resolves one layer of
        # escaping (turning the feed's literal "&amp;lt;p&amp;gt;" source
        # bytes into a real "<p>" for description's tags) - but DOU's own
        # title text separately contains genuine HTML-entity-escaped
        # characters within itself (e.g. a literal "&" rendered as
        # "&amp;" - confirmed live: "R&amp;D" survives ElementTree's
        # parse unchanged, needing this explicit unescape to become
        # "R&D"). description does NOT get unescaped here - it's left
        # exactly as raw as ArbeitnowJob.description is, so the adapter's
        # strip_html() (which does its own unescape as its first step,
        # same contract as Arbeitnow's) is the only place that happens,
        # not duplicated here.
        raw_title = unescape(title_el.text)
        title, company, location, remote, salary_min, salary_max, salary_currency = _parse_title(raw_title)

        description_el = item.find("description")
        description = description_el.text if description_el is not None and description_el.text else ""

        pubdate_el = item.find("pubDate")
        created_at = None
        if pubdate_el is not None and pubdate_el.text:
            try:
                created_at = int(parsedate_to_datetime(pubdate_el.text).timestamp())
            except (TypeError, ValueError, OverflowError):
                created_at = None

        return Job(
            external_id=id_match.group(1),
            title=title,
            company=company,
            location=location,
            remote=remote,
            url=url,
            description=description,
            created_at=created_at,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=salary_currency,
        )

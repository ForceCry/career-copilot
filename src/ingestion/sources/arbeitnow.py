import re
from datetime import datetime

from arbeitnow_client import ArbeitnowClient
from arbeitnow_client import Job as ArbeitnowJob

from ..html_strip import strip_html
from ..models import Vacancy
from .base import VacancySource

# Confirmed live against every one of 164 real ingested Arbeitnow postings:
# 100% of descriptions arrived as HTML-entity-escaped markup (React-rendered
# <div>/<p> tags with CSS-in-JS class names like "sc-gEkIjz cjtSrT
# MuiTypography-root MuiTypography-body1" mixed into the text); none of
# Adzuna's or justjoin.it's did. Left as-is, that markup soup burns a large
# chunk of TEI's 512-token embedding budget (empirically, 34% of all
# ingested vacancies get silently truncated by TEI's auto_truncate) and the
# cover-letter LLM prompt's 3000-char budget on CSS class names instead of
# actual job content. strip_html (src/ingestion/html_strip.py) is shared
# with DOU.ua's source, whose RSS <description> is HTML for the same
# reason - both get the same tag-stripping treatment.


# Arbeitnow itself appends this to every description - not written by the
# employer, just the platform's own footer. Confirmed live against all 192
# real ingested Arbeitnow postings: 100% end in one of exactly 4 variants
# ("Find Jobs in Germany on Arbeitnow", "Find more English Speaking Jobs in
# United Kingdom on Arbeitnow", etc.) - country name varies, the "Find ...
# Jobs in ... on Arbeitnow" shape doesn't. Deliberately narrow (letters/
# spaces only for the country, anchored to the end of the string) rather
# than a loose wildcard, so it can't accidentally eat real trailing content
# that happens to mention "jobs" or a place name.
_FOOTER_RE = re.compile(r"\s*Find(?: more [A-Za-z]+ Speaking)? Jobs in [A-Za-z ]+ on Arbeitnow\.?\s*$")


def _strip_footer(text: str) -> str:
    return _FOOTER_RE.sub("", text).strip()


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
            description=_strip_footer(strip_html(job.description)),
            tags=job.tags,
            created_at=datetime.fromtimestamp(job.created_at) if job.created_at else None,
        )

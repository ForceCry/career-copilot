import re
from datetime import datetime

from dou_client import DouClient
from dou_client import Job as DouJob

from ..html_strip import strip_html
from ..models import Vacancy
from .base import VacancySource

# DOU.ua's own platform appends this "apply" link block to every posting's
# description itself - not written by the employer, same category of
# noise as Arbeitnow's platform footer. Confirmed live against a real
# fetch of the PHP category feed (25/25 postings): every single one ends
# in this exact phrase after HTML-stripping - DOU always renders the same
# literal anchor text ("Відгукнутись на вакансію" = "Apply for the
# vacancy"), unlike Arbeitnow's 4 country-varying footer variants, so a
# single fixed phrase match is enough here.
_FOOTER_RE = re.compile(r"\s*Відгукнутись на вакансію\.?\s*$")


def _strip_footer(text: str) -> str:
    return _FOOTER_RE.sub("", text).strip()


class DouSource(VacancySource):
    """Thin adapter around the standalone dou-client library - maps its
    Job model onto career-copilot's internal Vacancy DTO. All the actual
    feed-reading/title-parsing logic lives in that library now; see its
    README for what was learned building it (in particular: why RSS, and
    the ~25-latest-postings-only limitation)."""

    name = "dou"

    def __init__(self, **client_kwargs):
        self._client = DouClient(**client_kwargs)

    def fetch(self, keywords: list[str], location: str) -> list[Vacancy]:
        return [self._to_vacancy(job) for job in self._client.search(keywords, location)]

    @staticmethod
    def _to_vacancy(job: DouJob) -> Vacancy:
        return Vacancy(
            source="dou",
            external_id=job.external_id,
            title=job.title,
            company=job.company,
            location=job.location,
            remote=job.remote,
            url=job.url,
            # unescape_first=False: DOU's description text arrives via
            # ElementTree, which already resolved the XML-level escaping
            # into real tags on its own (unlike Arbeitnow's raw JSON
            # value) - see strip_html's docstring for the real bug this
            # avoids (a manual pre-unescape turned literal "Span&lt;T&gt;"
            # prose into an actual "<T>" tag, silently deleting it).
            description=_strip_footer(strip_html(job.description, unescape_first=False)),
            tags=job.tags,
            created_at=datetime.fromtimestamp(job.created_at) if job.created_at else None,
            salary_min=job.salary_min,
            salary_max=job.salary_max,
            salary_currency=job.salary_currency,
            # Confirmed live: every DOU salary figure is a monthly net rate -
            # the standard convention across Ukrainian IT job postings, never
            # explicitly labeled as such in the feed itself, so this is an
            # assumption baked in here rather than something parsed from the
            # data. Only set when there's an actual salary to attach it to -
            # checking salary_min alone missed an upper-bound-only figure
            # ("до $X" - "up to $X", salary_min is None but salary_max isn't),
            # flagged by an independent Codex review.
            salary_period="month" if job.salary_min is not None or job.salary_max is not None else "",
        )

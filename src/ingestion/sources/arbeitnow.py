from datetime import datetime
from html import unescape
from html.parser import HTMLParser

from arbeitnow_client import ArbeitnowClient
from arbeitnow_client import Job as ArbeitnowJob

from ..models import Vacancy
from .base import VacancySource

# Block-level tags to break on when flattening to plain text, so words from
# adjacent elements don't get jammed together ("<p>Hello</p><p>World</p>"
# shouldn't become "HelloWorld"). td/th included - flagged by an independent
# Codex review: without them, adjacent table cells ("<td>Python</td>
# <td>Django</td>") concatenated into a single invented token ("PythonDjango")
# instead of two real ones.
_BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "td", "th",
}

# Tags whose content is never visible text - flagged by an independent
# Codex review: the original version had no notion of this, so a <style>
# or <script> tag's raw CSS/JS body was appended to the extracted text
# just like real content, reintroducing exactly the token-budget-wasting
# noise this whole function exists to remove. template/head added in a
# follow-up round of the same review: a <template> tag's content is
# never rendered by definition, and a <head>/<title> only shows up if
# some source ever hands us a full HTML document rather than a fragment
# - neither should count as the posting's visible text either.
# Deliberately NOT blanket-ignoring noscript/iframe/svg - their content
# can be conditionally or genuinely visible, unlike these four.
_IGNORE_CONTENT_TAGS = {"script", "style", "template", "head"}


class _TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self._ignore_depth = 0

    def handle_data(self, data: str) -> None:
        if self._ignore_depth == 0:
            self.chunks.append(data)

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _IGNORE_CONTENT_TAGS:
            self._ignore_depth += 1
        elif tag in _BLOCK_TAGS:
            self.chunks.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _IGNORE_CONTENT_TAGS:
            self._ignore_depth = max(0, self._ignore_depth - 1)
        elif tag in _BLOCK_TAGS:
            self.chunks.append(" ")


def _strip_html(raw: str) -> str:
    """Arbeitnow's API returns descriptions as raw, HTML-entity-escaped
    markup - React-rendered <div>/<p> tags with CSS-in-JS class names
    like "sc-gEkIjz cjtSrT MuiTypography-root MuiTypography-body1" mixed
    into the text. Confirmed live against every one of 164 real ingested
    Arbeitnow postings: 100% contained it; none of Adzuna's or
    justjoin.it's did. Left as-is, that markup soup burns a large chunk
    of TEI's 512-token embedding budget (empirically, 34% of all ingested
    vacancies get silently truncated by TEI's auto_truncate) and the
    cover-letter LLM prompt's 3000-char budget on CSS class names instead
    of actual job content. Unescape first (the response is HTML-entity-
    escaped on top of being HTML), then strip tags via the stdlib
    HTMLParser - no new dependency for what's ultimately just tag
    stripping.

    extractor.close() is required, not optional - flagged by an
    independent Codex review: HTMLParser buffers trailing content it
    hasn't fully parsed yet (an incomplete tag, or an entity reference
    missing its terminating ";") in case more data is about to be fed,
    and only flushes that buffer on close(). Without it, a description
    ending mid-entity - confirmed live: `_strip_html("visible &amp")`
    returned "" instead of "visible &" - could silently lose real
    trailing content, or in the worst case the whole string."""
    extractor = _TextExtractor()
    extractor.feed(unescape(raw))
    extractor.close()
    return " ".join("".join(extractor.chunks).split())


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
            description=_strip_html(job.description),
            tags=job.tags,
            created_at=datetime.fromtimestamp(job.created_at) if job.created_at else None,
        )

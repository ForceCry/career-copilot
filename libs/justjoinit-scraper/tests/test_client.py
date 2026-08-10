import json

import httpx

from justjoinit_scraper.client import JustJoinItScraper

SITEMAP_INDEX = """<?xml version="1.0" encoding="utf-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://justjoin.it/sitemaps/active-jobs/part0.xml</loc></sitemap>
</sitemapindex>"""

SITEMAP_PART = """<?xml version="1.0" encoding="utf-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://justjoin.it/job-offer/acme-php-developer</loc></url>
  <url><loc>https://justjoin.it/job-offer/acme-php-broken</loc></url>
  <url><loc>https://justjoin.it/job-offer/acme-php-bad-date</loc></url>
  <url><loc>https://justjoin.it/job-offer/acme-php-multi-ld</loc></url>
  <url><loc>https://justjoin.it/job-offer/other-react-developer</loc></url>
</urlset>"""


def _job_page(job_ld: dict) -> str:
    return f'<html><body><script type="application/ld+json">{json.dumps(job_ld)}</script></body></html>'


GOOD_JOB_LD = {
    "title": "Senior PHP Developer",
    "hiringOrganization": {"name": "Acme"},
    "jobLocation": {"address": {"addressLocality": "Warszawa", "addressCountry": "PL"}},
    "jobLocationType": "TELECOMMUTE",
    "description": "PHP/Symfony role.",
    "datePosted": "2026-01-01T00:00:00Z",
    "baseSalary": None,  # confirmed live: real postings do have this as an explicit null
}


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == "https://justjoin.it/sitemaps/active-jobs.xml":
        return httpx.Response(200, content=SITEMAP_INDEX)
    if url == "https://justjoin.it/sitemaps/active-jobs/part0.xml":
        return httpx.Response(200, content=SITEMAP_PART)
    if url == "https://justjoin.it/job-offer/acme-php-developer":
        return httpx.Response(200, text=_job_page(GOOD_JOB_LD))
    if url == "https://justjoin.it/job-offer/acme-php-broken":
        return httpx.Response(
            200, text='<html><script type="application/ld+json">{not valid json</script></html>'
        )
    if url == "https://justjoin.it/job-offer/acme-php-bad-date":
        bad_date_ld = {**GOOD_JOB_LD, "title": "PHP Developer Bad Date", "datePosted": "not-a-real-date"}
        return httpx.Response(200, text=_job_page(bad_date_ld))
    if url == "https://justjoin.it/job-offer/acme-php-multi-ld":
        breadcrumb_ld = {"@type": "BreadcrumbList", "itemListElement": []}
        job_ld = {**GOOD_JOB_LD, "@type": "JobPosting", "title": "PHP Developer Multi LD"}
        return httpx.Response(
            200,
            text=(
                "<html><body>"
                f'<script type="application/ld+json">{json.dumps(breadcrumb_ld)}</script>'
                f"<script type='application/ld+json'>{json.dumps(job_ld)}</script>"
                "</body></html>"
            ),
        )
    return httpx.Response(404)


def _patch_httpx_client(monkeypatch, handler):
    real_client_cls = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_null_base_salary_does_not_crash(monkeypatch):
    """Regression: real JobPosting JSON-LD can have baseSalary present but
    explicitly null - .get("baseSalary", {}) doesn't catch that (the
    default only applies when the key is missing), so this used to raise
    AttributeError and abort the whole search."""
    _patch_httpx_client(monkeypatch, _handler)
    scraper = JustJoinItScraper(request_delay=0)

    jobs = scraper.search(["php"], "Warsaw")

    titles = {j.title for j in jobs}
    assert "Senior PHP Developer" in titles
    matched = next(j for j in jobs if j.title == "Senior PHP Developer")
    assert matched.salary_min is None
    assert matched.salary_currency == ""


def test_one_malformed_posting_does_not_abort_the_batch(monkeypatch):
    """Regression: a single bad JSON-LD block used to raise and lose every
    already-fetched result, not just the one bad posting."""
    _patch_httpx_client(monkeypatch, _handler)
    scraper = JustJoinItScraper(request_delay=0)

    jobs = scraper.search(["php"], "Warsaw")
    titles = {j.title for j in jobs}

    # acme-php-broken (invalid JSON) is skipped; the rest still come back.
    assert "Senior PHP Developer" in titles


def test_validation_error_does_not_abort_the_batch(monkeypatch):
    """Regression: a posting whose JSON-LD parses fine but fails Job's own
    field validation (e.g. an unparseable datePosted) raised
    pydantic.ValidationError, which the original except tuple didn't
    catch - it aborted the whole search, not just that one posting.
    Flagged by an independent Codex re-review."""
    _patch_httpx_client(monkeypatch, _handler)
    scraper = JustJoinItScraper(request_delay=0)

    jobs = scraper.search(["php"], "Warsaw")
    titles = {j.title for j in jobs}

    assert "PHP Developer Bad Date" not in titles  # the invalid one is skipped
    assert "Senior PHP Developer" in titles  # not lost along with it


def test_multiple_json_ld_blocks_picks_the_job_posting_type(monkeypatch):
    """Regression: a page with more than one JSON-LD block (e.g.
    breadcrumbs alongside the job posting) used to convert whichever one
    the regex matched first - here, the non-JobPosting block - into a
    mostly-empty Job instead of the real posting."""
    _patch_httpx_client(monkeypatch, _handler)
    scraper = JustJoinItScraper(request_delay=0)

    jobs = scraper.search(["php"], "Warsaw")
    titles = {j.title for j in jobs}

    assert "PHP Developer Multi LD" in titles

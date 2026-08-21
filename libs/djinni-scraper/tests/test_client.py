import json

import httpx

from djinni_scraper.client import DjinniScraper

GOOD_JOB_LD = {
    "@type": "JobPosting",
    "identifier": 358246,
    "title": "Senior PHP Developer",
    "hiringOrganization": {"name": "Acme"},
    "jobLocation": {"address": {"addressLocality": ["Kyiv"], "addressCountry": "Ukraine"}},
    "description": "PHP/Symfony role.",
    "datePosted": "2026-01-01T00:00:00",
    "url": "https://djinni.co/jobs/358246-senior-php-developer/",
    "baseSalary": None,  # confirmed live: real postings do have this as an explicit null
}


def _listing_page(paths: list[str]) -> str:
    links = "".join(f'<a href="{p}">job</a>' for p in paths)
    return f"<html><body>{links}</body></html>"


def _job_page(job_ld: dict) -> str:
    return f'<html><body><script type="application/ld+json">{json.dumps(job_ld)}</script></body></html>'


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.startswith("https://djinni.co/jobs/?"):
        page = request.url.params.get("page", "1")
        if page == "1":
            return httpx.Response(200, text=_listing_page(["/jobs/358246-senior-php-developer/"]))
        return httpx.Response(200, text=_listing_page([]))  # empty - end of pagination
    if url == "https://djinni.co/jobs/358246-senior-php-developer/":
        return httpx.Response(200, text=_job_page(GOOD_JOB_LD))
    if url == "https://djinni.co/jobs/900001-broken/":
        return httpx.Response(
            200, text='<html><script type="application/ld+json">{not valid json</script></html>'
        )
    if url == "https://djinni.co/jobs/900002-bad-date/":
        bad_date_ld = {**GOOD_JOB_LD, "title": "PHP Developer Bad Date", "datePosted": "not-a-real-date"}
        return httpx.Response(200, text=_job_page(bad_date_ld))
    if url == "https://djinni.co/jobs/900003-multi-ld/":
        breadcrumb_ld = {"@type": "BreadcrumbList", "itemListElement": []}
        job_ld = {**GOOD_JOB_LD, "title": "PHP Developer Multi LD"}
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


def test_search_discovers_and_parses_a_real_shaped_posting(monkeypatch):
    _patch_httpx_client(monkeypatch, _handler)
    scraper = DjinniScraper(request_delay=0)

    jobs = scraper.search(["php"], "Warsaw")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "358246"
    assert job.title == "Senior PHP Developer"
    assert job.company == "Acme"
    assert job.location == "Kyiv, Ukraine"
    assert job.remote is False
    assert job.salary_min is None  # explicit-null baseSalary handled, not a crash
    assert job.salary_currency == ""


def test_search_stops_pagination_on_a_redirect_instead_of_following_it(monkeypatch):
    """Regression: an independent Codex review found live that a page
    number past the real end of results 302-redirects to the UNFILTERED
    /jobs/ listing, not an empty page - confirmed the redirect target
    still has real job links, just for every category, not the
    configured primary_keyword. With follow_redirects=True (needed for
    detail-page fetches), the original code treated that as more real
    results and silently pulled in unrelated postings mislabeled as this
    scraper's keyword. A redirect during listing pagination must stop
    the loop, not be followed and counted."""
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested.append(url)
        if url.startswith("https://djinni.co/jobs/?"):
            page = request.url.params.get("page", "1")
            if page == "1":
                return httpx.Response(200, text=_listing_page(["/jobs/358246-senior-php-developer/"]))
            # Simulate the real out-of-range behavior: redirect to the
            # unfiltered listing, which itself has real (but unrelated)
            # job links - not an empty page.
            return httpx.Response(302, headers={"location": "/jobs/"}, request=request)
        if url == "https://djinni.co/jobs/":
            # What the redirect resolves to, per httpx's follow_redirects.
            return httpx.Response(200, text=_listing_page(["/jobs/900099-unrelated-role/"]))
        return _handler(request)

    _patch_httpx_client(monkeypatch, handler)
    jobs = DjinniScraper(request_delay=0, max_pages=5).search(["php"], "Warsaw")

    # The unrelated posting discovered on the redirect's destination must
    # never even be fetched - pagination stopped before considering it.
    assert not any("900099" in u for u in requested)
    assert len(jobs) == 1
    assert jobs[0].title == "Senior PHP Developer"


def test_search_stops_pagination_on_an_empty_page(monkeypatch):
    calls = {"pages": []}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://djinni.co/jobs/?"):
            page = request.url.params.get("page", "1")
            calls["pages"].append(page)
            return httpx.Response(200, text=_listing_page([]))
        return httpx.Response(404)

    _patch_httpx_client(monkeypatch, handler)
    DjinniScraper(request_delay=0, max_pages=5).search([], "")

    assert calls["pages"] == ["1"]  # never reaches page 2 - the first page was already empty


def test_null_base_salary_does_not_crash(monkeypatch):
    """Regression-shaped like the same real gotcha justjoinit-scraper
    already hit: real JobPosting JSON-LD can have baseSalary present but
    explicitly null - .get("baseSalary", {}) doesn't catch that (the
    default only applies when the key is missing)."""
    _patch_httpx_client(monkeypatch, _handler)
    jobs = DjinniScraper(request_delay=0).search(["php"], "Warsaw")

    assert jobs[0].salary_min is None


def test_one_malformed_posting_does_not_abort_the_batch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://djinni.co/jobs/?"):
            page = request.url.params.get("page", "1")
            if page == "1":
                return httpx.Response(
                    200,
                    text=_listing_page(["/jobs/900001-broken/", "/jobs/358246-senior-php-developer/"]),
                )
            return httpx.Response(200, text=_listing_page([]))
        return _handler(request)

    _patch_httpx_client(monkeypatch, handler)
    jobs = DjinniScraper(request_delay=0).search(["php"], "Warsaw")

    titles = {j.title for j in jobs}
    assert "Senior PHP Developer" in titles


def test_validation_error_does_not_abort_the_batch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://djinni.co/jobs/?"):
            page = request.url.params.get("page", "1")
            if page == "1":
                return httpx.Response(
                    200,
                    text=_listing_page(["/jobs/900002-bad-date/", "/jobs/358246-senior-php-developer/"]),
                )
            return httpx.Response(200, text=_listing_page([]))
        return _handler(request)

    _patch_httpx_client(monkeypatch, handler)
    jobs = DjinniScraper(request_delay=0).search(["php"], "Warsaw")
    titles = {j.title for j in jobs}

    assert "PHP Developer Bad Date" not in titles
    assert "Senior PHP Developer" in titles


def test_multiple_json_ld_blocks_picks_the_job_posting_type(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://djinni.co/jobs/?"):
            page = request.url.params.get("page", "1")
            if page == "1":
                return httpx.Response(200, text=_listing_page(["/jobs/900003-multi-ld/"]))
            return httpx.Response(200, text=_listing_page([]))
        return _handler(request)

    _patch_httpx_client(monkeypatch, handler)
    jobs = DjinniScraper(request_delay=0).search(["php"], "Warsaw")

    assert len(jobs) == 1
    assert jobs[0].title == "PHP Developer Multi LD"


def test_external_id_comes_from_the_url_not_the_json_ld_identifier():
    """Regression: an independent Codex review found the original code
    used the JSON-LD's own `identifier` field for external_id, which
    schema.org permits to be either a bare scalar or a PropertyValue
    object (str() on the latter would persist the dict's repr, not a
    usable id), and which isn't guaranteed present on every fetch of the
    same real posting - either way risking two different external_id
    values for the same posting across two runs, which upsert_vacancies
    (keyed on (source, external_id)) would then store as two separate
    rows instead of one. The URL's own numeric id - already required
    just to discover this posting via _JOB_LINK_RE - is deterministic
    and always available, so external_id is derived from that instead,
    regardless of what (if anything) the JSON-LD's `identifier` says."""
    job_posting = {**GOOD_JOB_LD, "identifier": {"@type": "PropertyValue", "value": "not-the-url-id"}}
    job = DjinniScraper._to_job("https://djinni.co/jobs/358246-senior-php-developer/", job_posting)

    assert job.external_id == "358246"


def test_addressLocality_as_a_plain_string_is_handled_too():
    """Confirmed live: most real postings have addressLocality as a list
    (["Kyiv"]), but the client shouldn't assume every source always
    shapes it that way - handle a plain string too rather than crashing
    on join()."""
    address = {"addressLocality": "Lviv", "addressCountry": "Ukraine"}
    job_posting = {**GOOD_JOB_LD, "jobLocation": {"address": address}}
    job = DjinniScraper._to_job("https://djinni.co/jobs/900004-x/", job_posting)

    assert job.location == "Lviv, Ukraine"

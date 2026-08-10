import httpx
import pytest

from adzuna_client.client import AdzunaClient
from adzuna_client.errors import AdzunaAPIError

RAW_JOB = {
    "id": 12345,
    "title": "Senior PHP Developer",
    "company": {"display_name": "Acme Sp. z o.o."},
    "location": {"display_name": "Warszawa, mazowieckie"},
    "redirect_url": "https://www.adzuna.pl/land/ad/12345",
    "description": "We need a PHP developer.",
    "category": {"label": "IT Jobs"},
    "created": "2026-01-01T00:00:00Z",
    "salary_min": 180000,
    "salary_max": 240000,
    "salary_is_predicted": "0",
}


def _patch_httpx_client(monkeypatch, handler):
    real_client_cls = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_search_maps_job_fields(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [RAW_JOB]})

    _patch_httpx_client(monkeypatch, handler)
    client = AdzunaClient(app_id="id", app_key="key", country="pl", max_pages=1)

    jobs = client.search(["php"], "Warsaw")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "12345"
    assert job.title == "Senior PHP Developer"
    assert job.company == "Acme Sp. z o.o."
    assert job.salary_min == 180000
    assert job.salary_currency == "PLN"
    assert job.salary_period == "year"
    assert job.salary_is_predicted is False


def test_search_covers_all_supported_countries(monkeypatch):
    """Regression: only 4 of ~19 country endpoints had a currency mapped -
    the rest silently got an empty salary_currency."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [RAW_JOB]})

    _patch_httpx_client(monkeypatch, handler)

    for country in ["at", "au", "br", "ca", "fr", "in", "it", "mx", "nl", "nz", "sg", "za", "es", "se", "ch"]:
        client = AdzunaClient(app_id="id", app_key="key", country=country, max_pages=1)
        jobs = client.search(["php"], "somewhere")
        assert jobs[0].salary_currency, f"no currency mapped for country={country}"


def test_error_response_does_not_leak_credentials(monkeypatch):
    """Regression: the first fix only redacted the exception's message
    text but still attached the original httpx.Request/Response (with
    the raw URL) to the raised error, so e.request.url still leaked the
    credential even though str(e) didn't - confirmed by an independent
    Codex review. Raising a plain AdzunaAPIError with no reference to the
    original request/response closes that off entirely: there's no
    request/response attribute left to inspect."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    _patch_httpx_client(monkeypatch, handler)
    client = AdzunaClient(app_id="my-app-id", app_key="my-secret-key", max_pages=1)

    with pytest.raises(AdzunaAPIError) as exc_info:
        client.search(["php"], "Warsaw")

    error = exc_info.value
    assert error.status_code == 403
    assert not hasattr(error, "request")
    assert not hasattr(error, "response")
    assert "my-secret-key" not in str(error)
    assert "my-app-id" not in str(error)


def test_error_response_redacts_url_encoded_credentials(monkeypatch):
    """The naive fix used str.replace(raw_key, "***") on the URL text,
    which misses a credential value httpx percent-encoded (e.g. it
    contains "+", "/", "="). Rebuilding from parsed query params instead
    of raw text redacts the encoded form too."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    _patch_httpx_client(monkeypatch, handler)
    client = AdzunaClient(app_id="id", app_key="se+cr/et=key", max_pages=1)

    with pytest.raises(AdzunaAPIError) as exc_info:
        client.search(["php"], "Warsaw")

    assert "se+cr/et=key" not in str(exc_info.value)
    assert "se%2Bcr%2Fet%3Dkey" not in str(exc_info.value)

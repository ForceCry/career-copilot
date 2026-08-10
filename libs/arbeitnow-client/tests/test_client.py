import httpx

from arbeitnow_client.client import ArbeitnowClient

PHP_JOB = {
    "slug": "acme-php-dev",
    "company_name": "Acme",
    "title": "Senior PHP Developer",
    "description": "We need Symfony experience.",
    "remote": True,
    "url": "https://www.arbeitnow.com/jobs/acme-php-dev",
    "tags": ["PHP", "Symfony"],
    "job_types": ["Full-time"],
    "location": "Berlin",
    "created_at": 1700000000,
}
UNRELATED_JOB = {
    "slug": "acme-react-dev",
    "company_name": "Acme",
    "title": "React Frontend Engineer",
    "description": "TypeScript, CSS.",
    "remote": False,
    "url": "https://www.arbeitnow.com/jobs/acme-react-dev",
    "tags": ["React"],
    "job_types": ["Full-time"],
    "location": "Berlin",
    "created_at": 1700000000,
}


def _patch_httpx_client(monkeypatch, handler):
    real_client_cls = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_search_filters_by_keyword_client_side(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [PHP_JOB, UNRELATED_JOB], "links": {"next": None}})

    _patch_httpx_client(monkeypatch, handler)
    client = ArbeitnowClient(max_pages=1)

    jobs = client.search(["php"], "Berlin")

    assert len(jobs) == 1
    assert jobs[0].title == "Senior PHP Developer"
    assert jobs[0].external_id == "acme-php-dev"
    assert jobs[0].remote is True
    assert jobs[0].created_at == 1700000000


def test_search_stops_paginating_when_next_is_none(monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json={"data": [PHP_JOB], "links": {"next": None}})

    _patch_httpx_client(monkeypatch, handler)
    client = ArbeitnowClient(max_pages=5, request_delay=0)

    client.search(["php"], "Berlin")

    assert calls["n"] == 1


def test_search_refuses_to_follow_next_link_to_a_different_host(monkeypatch):
    """Regression: links.next from the API response was followed
    unconditionally - a compromised/malformed response could redirect
    this client to an arbitrary host, including internal network
    endpoints (SSRF), flagged by an independent Codex review."""
    requested_hosts = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_hosts.append(request.url.host)
        if len(requested_hosts) == 1:
            return httpx.Response(
                200,
                json={
                    "data": [PHP_JOB],
                    "links": {"next": "https://evil.example.com/internal-metadata"},
                },
            )
        return httpx.Response(200, json={"data": [], "links": {"next": None}})

    _patch_httpx_client(monkeypatch, handler)
    client = ArbeitnowClient(max_pages=5, request_delay=0)

    client.search(["php"], "Berlin")

    assert requested_hosts == ["www.arbeitnow.com"]  # never followed the redirect to evil.example.com


def test_search_refuses_non_https_next_link(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [PHP_JOB], "links": {"next": "http://www.arbeitnow.com/api/job-board-api?page=2"}}
        )

    _patch_httpx_client(monkeypatch, handler)
    client = ArbeitnowClient(max_pages=5, request_delay=0)

    jobs = client.search(["php"], "Berlin")

    assert len(jobs) == 1  # first page only - the http:// next link was rejected

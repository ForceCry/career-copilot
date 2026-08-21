import time
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from djinni_scraper.retry import MAX_RETRY_AFTER_SECONDS, _parse_retry_after, get_with_retry


def test_retries_and_recovers_on_429():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(429, headers={"retry-after": "0"})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = get_with_retry(client, "https://example.test/jobs")

    assert response.status_code == 200
    assert attempts["n"] == 3


def test_retry_after_http_date_does_not_raise():
    """Regression: Retry-After can be an HTTP-date, not just seconds -
    float(retry_after) used to raise ValueError on this input, as an
    independent Codex review confirmed live against this exact code."""
    attempts = {"n": 0}
    future = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=0), usegmt=True)

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(429, headers={"retry-after": future})
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = get_with_retry(client, "https://example.test/jobs")

    assert response.status_code == 200


def test_gives_up_after_max_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = get_with_retry(client, "https://example.test/jobs", max_retries=2, backoff_seconds=0)

    assert response.status_code == 500


@pytest.mark.parametrize("raw", ["-1", "nan", "-inf"])
def test_invalid_numeric_retry_after_falls_back_to_backoff(raw):
    """Regression: float("-1")/"nan" parsed "successfully" and reached
    time.sleep(), which raises for both - confirmed live via an
    independent Codex review."""
    assert _parse_retry_after(raw) is None


def test_retry_after_seconds_is_capped():
    assert _parse_retry_after("999999999") == MAX_RETRY_AFTER_SECONDS
    assert _parse_retry_after("inf") is None  # not finite - backoff, not even the cap


def test_transport_error_is_retried():
    """Regression: a connection/timeout error used to raise straight
    through with zero retry, despite this being an idempotent GET and
    the whole point of this helper - flagged by an independent Codex
    review."""
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ConnectError("simulated network failure", request=request)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    response = get_with_retry(client, "https://example.test/jobs", backoff_seconds=0)

    assert response.status_code == 200
    assert attempts["n"] == 3


def test_transport_error_raises_after_max_retries():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("simulated network failure", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ConnectError):
        get_with_retry(client, "https://example.test/jobs", max_retries=1, backoff_seconds=0)


def test_get_with_retry_does_not_hang_on_malicious_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "inf"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    start = time.monotonic()
    get_with_retry(client, "https://example.test/jobs", max_retries=1, backoff_seconds=0)
    elapsed = time.monotonic() - start

    assert elapsed < 5

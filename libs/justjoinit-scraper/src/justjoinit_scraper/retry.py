import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}

# A server-supplied Retry-After is honored, but not blindly: capped so a
# malformed or malicious value (or a legitimately huge one) can't block a
# synchronous call for hours - confirmed live (via an independent Codex
# review of this exact code, shared origin with the other two extracted
# clients) that float("-1")/"nan" reach time.sleep() and raise, and
# float("inf") either raises OverflowError or hangs depending on platform.
MAX_RETRY_AFTER_SECONDS = 120.0


def _parse_retry_after(value: str) -> float | None:
    """Retry-After is either delay-seconds ("120") or an HTTP-date
    ("Fri, 07 Nov 2025 23:59:59 GMT") per RFC 7231 - servers are free to
    send either."""
    try:
        seconds = float(value)
    except ValueError:
        seconds = None
    else:
        if not math.isfinite(seconds) or seconds < 0:
            seconds = None  # fall through to backoff rather than sleep(-1)/sleep(nan)/sleep(inf)

    if seconds is None:
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        seconds = max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())

    return min(seconds, MAX_RETRY_AFTER_SECONDS)


def get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    max_retries: int = 3,
    backoff_seconds: float = 1.0,
) -> httpx.Response:
    """GET with exponential backoff on 429/5xx and on transport-level
    failures (timeouts, connection resets, DNS errors) - the latter used
    to raise straight through despite an idempotent GET and a helper
    whose whole purpose is retry/backoff, flagged by an independent Codex
    review. Honors a server's Retry-After header when present instead of
    guessing at a delay for the status-code case. Caller still calls
    response.raise_for_status()."""
    response: httpx.Response | None = None

    for attempt in range(max_retries + 1):
        try:
            response = client.get(url, params=params)
        except httpx.TransportError:
            if attempt == max_retries:
                raise
            time.sleep(backoff_seconds * (2**attempt))
            continue

        if response.status_code not in RETRYABLE_STATUSES or attempt == max_retries:
            return response

        retry_after = response.headers.get("retry-after")
        parsed = _parse_retry_after(retry_after) if retry_after else None
        delay = parsed if parsed is not None else backoff_seconds * (2**attempt)
        time.sleep(delay)

    return response

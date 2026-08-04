import time

import httpx

RETRYABLE_STATUSES = {429, 500, 502, 503, 504}


def get_with_retry(
    client: httpx.Client,
    url: str,
    params: dict | None = None,
    max_retries: int = 3,
    backoff_seconds: float = 1.0,
) -> httpx.Response:
    """GET with exponential backoff on 429/5xx. Honors a server's
    Retry-After header when present (Arbeitnow's Cloudflare challenge
    sends one) instead of guessing at a delay. Caller still calls
    response.raise_for_status() - this only handles the retry loop."""
    response = client.get(url, params=params)

    for attempt in range(max_retries):
        if response.status_code not in RETRYABLE_STATUSES:
            return response

        retry_after = response.headers.get("retry-after")
        delay = float(retry_after) if retry_after else backoff_seconds * (2**attempt)
        time.sleep(delay)
        response = client.get(url, params=params)

    return response

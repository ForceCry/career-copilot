# arbeitnow-client

Minimal Python client for the [Arbeitnow](https://www.arbeitnow.com/api/job-board-api)
job board API. Extracted from a larger job-matching project
(career-copilot) where it was one of three ingestion sources.

## Usage

```python
from arbeitnow_client import ArbeitnowClient

client = ArbeitnowClient()
jobs = client.search(keywords=["php", "symfony"], location="Warsaw")

for job in jobs:
    print(job.title, job.company, job.remote)
```

## Notes from building this against the live API

- No API key required.
- `search`/`tags` query params exist in the API but are ignored
  server-side - confirmed by hand, not assumed from docs. Filtering
  happens client-side here, after fetching.
- `location` is accepted by `search()` for interface consistency with
  other job clients, but the API has no server-side location filter and
  this client doesn't fake one - don't expect it to narrow results.
- No salary field in the API at all, unlike some other job boards.
- Rate limiting is real: a handful of quick requests in a row triggered
  a 429 behind a Cloudflare challenge during testing. The client paces
  requests and retries with backoff by default.

## Install

```bash
pip install -e /path/to/arbeitnow-client
```

Not published to PyPI (yet) - install from a local path or a git URL.

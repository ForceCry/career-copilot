# adzuna-client

Minimal Python client for the [Adzuna](https://developer.adzuna.com/) job
search API. Extracted from a larger job-matching project (career-copilot)
where it was originally one of three ingestion sources - pulled out on its
own so it's usable without depending on that project.

## Usage

```python
from adzuna_client import AdzunaClient

client = AdzunaClient(app_id="...", app_key="...", country="pl")
jobs = client.search(keywords=["php", "symfony"], location="Warsaw")

for job in jobs:
    print(job.title, job.company, job.salary_min, job.salary_currency)
```

## Notes from building this against the live API

- `what` (keywords) are ANDed together, not ORed - `what="php symfony"`
  searches for postings containing both terms, not either. Pass a single
  keyword if you want a broader net.
- Salary figures are Adzuna's own convention: annualized estimates, not
  necessarily what the posting states directly. `salary_is_predicted`
  distinguishes an estimate from a stated figure - worth surfacing to
  users rather than presenting both the same way.
- No currency field in the API response; it's implied by which
  country-specific endpoint you queried. `COUNTRY_CURRENCY` in
  `client.py` covers a few common ones - extend as needed.
- One `app_id`/`app_key` pair works across every country endpoint - no
  separate registration per country.

## Install

```bash
pip install -e /path/to/adzuna-client
```

Not published to PyPI (yet) - install from a local path or a git URL.

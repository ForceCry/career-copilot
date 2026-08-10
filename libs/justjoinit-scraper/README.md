# justjoinit-scraper

Reads job postings from [justjoin.it](https://justjoin.it) without touching
their private API. Extracted from a larger job-matching project
(career-copilot) where it was one of three ingestion sources.

## Why "scraper" and not "client"

justjoin.it has no public partner API. `api.justjoin.it` is the private
backend their own frontend calls, and their `robots.txt` explicitly
disallows crawling `/api/` - a clear signal not to touch it.

What they do publish deliberately:
- A sitemap of active job postings (`robots.txt` -> `Sitemap:
  .../active-jobs.xml`), meant for search engine indexing.
- A `schema.org` `JobPosting` JSON-LD block embedded in every job page,
  meant for Google Jobs and similar.

This reads only those two sanctioned surfaces. No private endpoints
touched - hence "scraper" (reading public pages) rather than "client"
(talking to an API meant for this).

## Usage

```python
from justjoinit_scraper import JustJoinItScraper

scraper = JustJoinItScraper()
jobs = scraper.search(keywords=["php", "symfony"], location="Warsaw")

for job in jobs:
    print(job.title, job.company, job.salary_min, job.salary_currency, job.salary_period)
```

## Notes from building this against the live site

- Slower than a real API by nature: no bulk search endpoint, so this
  fetches one full page (~850KB, mostly a JS bundle) per matching
  posting. Filtering on the sitemap's URL slugs first (cheap) keeps the
  number of full-page fetches down to only genuine matches - roughly 1%
  of active postings matched "php"/"symfony" when tested.
- `location` is accepted by `search()` for interface consistency with
  other job clients, but matching only happens against the URL slug,
  which encodes role/tech reliably but not location - don't expect it to
  narrow results by city.
- Salary comes from the JobPosting JSON-LD's `baseSalary` - includes an
  explicit `unitText` (MONTH/YEAR/HOUR), which matters: one real posting
  turned out to be a B2B hourly rate (100-140 PLN/hour), not monthly -
  reading the period instead of assuming one avoids a misleading number.
- Politeness matters here more than with a real API: `request_delay`
  paces the one-page-per-match fetches deliberately, not just to dodge
  rate limits.

## Install

```bash
pip install -e /path/to/justjoinit-scraper
```

Not published to PyPI (yet) - install from a local path or a git URL.

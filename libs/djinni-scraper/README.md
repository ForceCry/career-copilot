# djinni-scraper

Scraper for [djinni.co](https://djinni.co)'s public job listings - Djinni
has no partner API. This reads two surfaces it serves publicly and
without login: the search listing (`/jobs/?primary_keyword=<name>`, used
only to discover posting URLs - `robots.txt` allows `/jobs`, only
`/jobs2`/`/q`/`/developers`/`/free-jobs`/`/set_lang` are disallowed) and
each posting's own `schema.org` `JobPosting` JSON-LD block (meant for
Google Jobs indexing, same sanctioned-surface approach
`justjoinit-scraper` already uses for justjoin.it). No private/internal
API endpoints are touched.

Deliberately NOT scraping `/my/dashboard/` - that's a logged-in user's
own personalized recommendation feed (confirmed live: unauthenticated
access 302-redirects to a login page), not public data, and scraping it
would mean storing Djinni account credentials in this project (a new
category of secret nothing else here needs) plus likely running against
Djinni's own ToS around automated access to an authenticated area.
Djinni's own recommendation algorithm is also redundant with what this
project already does itself (semantic vector matching against the
user's actual profile) - the public listing gives raw vacancy data to
match against directly, same as every other source here.

## Known limitations

- No sitemap of individual postings (djinni.co/sitemap.xml only lists
  category *landing* pages, e.g. `/jobs/keyword-php`) - job URLs are
  discovered by paginating the search listing instead of reading a
  sitemap, unlike justjoin.it.
- `description` is already clean plain text in the JSON-LD (confirmed
  live across 15 real postings, zero HTML tags) - no HTML-stripping
  step needed, unlike Arbeitnow/DOU.
- `baseSalary` is present only when the poster disclosed one - confirmed
  live, most postings omit it entirely.
- No location-filter parameter - `location` is accepted by `search()`
  for interface consistency with the other job clients in this project,
  but not applied. `keywords` is also accepted but not applied -
  `primary_keyword` passed to `DjinniScraper()` is the only filter this
  actually supports.

Run: `pip install -e .[dev]` then `pytest`.

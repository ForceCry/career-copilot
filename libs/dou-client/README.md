# dou-client

Minimal client for [jobs.dou.ua](https://jobs.dou.ua)'s public vacancies RSS
feed - DOU.ua has no partner API, but does publish an official,
category-filterable RSS feed (`/vacancies/feeds/?category=<name>`, linked
from every category listing page), which is what this reads. Not a
scraper: this only touches a surface DOU.ua itself explicitly serves for
programmatic consumption.

## Known limitations

- The feed always returns the ~25 most recent postings for the category,
  regardless of any `count`/`page` query param tried (confirmed live -
  neither is honored). Fine for a recurring poll (new postings get
  picked up on the next run, same as every other source in this
  project), not suitable for a one-off full historical backfill.
- `title` packs company/location/remote/salary into one free-text string
  (DOU's own template: `"{title} в {company}[, ${salary}][, {city...}]
  [, за кордоном][, віддалено]"`) - parsed with a best-effort regex, not
  guaranteed to handle every future format DOU might introduce. A parse
  miss degrades gracefully (falls back to treating the whole remainder
  as the company name, no crash), never raises.
- No keyword or location filter parameter - both `keywords` and
  `location` are accepted by `search()` for interface consistency with
  the other job clients in this project, but neither is applied. The
  `category` passed to `DouClient()` is the only filter this feed
  actually supports.

Run: `pip install -e .[dev]` then `pytest`.

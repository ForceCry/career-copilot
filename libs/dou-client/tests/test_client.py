from email.utils import parsedate_to_datetime

import httpx

from dou_client.client import DouClient, _parse_title

RSS_TEMPLATE = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>Feed</title>
{items}
</channel></rss>"""

ITEM_TEMPLATE = """<item>
<title>{title}</title>
<link>{link}</link>
<description>{description}</description>
<pubDate>{pubdate}</pubDate>
<guid>{link}?123</guid>
</item>"""


def _rss(items: list[str]) -> str:
    return RSS_TEMPLATE.format(items="\n".join(items))


def _patch_httpx_client(monkeypatch, handler):
    real_client_cls = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client_cls(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", factory)


def test_search_parses_a_real_shaped_item(monkeypatch):
    item = ITEM_TEMPLATE.format(
        title="Senior PHP Developer в FAVBET, Київ, за кордоном, віддалено",
        link="https://jobs.dou.ua/companies/favbet/vacancies/369737/",
        description="&lt;p&gt;Great role.&lt;/p&gt;",
        pubdate="Wed, 19 Aug 2026 13:39:52 +0300",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["category"] == "PHP"
        return httpx.Response(200, content=_rss([item]).encode())

    _patch_httpx_client(monkeypatch, handler)
    jobs = DouClient().search(["php"], "Warsaw")

    assert len(jobs) == 1
    job = jobs[0]
    assert job.external_id == "369737"
    assert job.title == "Senior PHP Developer"
    assert job.company == "FAVBET"
    assert job.location == "Київ, за кордоном"
    assert job.remote is True
    assert job.url == "https://jobs.dou.ua/companies/favbet/vacancies/369737/"
    # ElementTree's own XML decode already turns the feed's escaped
    # "&lt;p&gt;...&lt;/p&gt;" bytes into a real "<p>...</p>" tag - this
    # client doesn't strip it further (that's strip_html()'s job in the
    # adapter), it's just not still XML-escaped by this point either.
    assert job.description == "<p>Great role.</p>"
    assert job.created_at == int(parsedate_to_datetime("Wed, 19 Aug 2026 13:39:52 +0300").timestamp())


def test_search_uses_the_configured_category(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["category"] = request.url.params["category"]
        return httpx.Response(200, content=_rss([]).encode())

    _patch_httpx_client(monkeypatch, handler)
    DouClient(category="Python").search([], "")

    assert seen["category"] == "Python"


def test_search_skips_an_item_with_no_recognizable_vacancy_id(monkeypatch):
    item = ITEM_TEMPLATE.format(
        title="Some Role в Some Co, віддалено",
        link="https://jobs.dou.ua/companies/some-co/",  # no /vacancies/<id>/ segment
        description="",
        pubdate="Wed, 19 Aug 2026 13:39:52 +0300",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_rss([item]).encode())

    _patch_httpx_client(monkeypatch, handler)
    jobs = DouClient().search([], "")

    assert jobs == []


def test_search_handles_an_unparseable_pubdate_without_raising(monkeypatch):
    item = ITEM_TEMPLATE.format(
        title="Some Role в Some Co, віддалено",
        link="https://jobs.dou.ua/companies/some-co/vacancies/1/",
        description="",
        pubdate="not a date",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_rss([item]).encode())

    _patch_httpx_client(monkeypatch, handler)
    jobs = DouClient().search([], "")

    assert jobs[0].created_at is None


def test_parse_title_falls_back_when_separator_is_missing():
    """No " в " at all - a title format DOU has never actually produced
    in any live sample, but this must degrade instead of raising, since
    it's parsing free text a third party fully controls."""
    title, company, location, remote, salary_min, salary_max, currency = _parse_title(
        "Malformed Title With No Separator"
    )

    assert title == "Malformed Title With No Separator"
    assert company == ""
    assert location == ""
    assert remote is False


def test_parse_title_extracts_salary_range():
    _, _, _, _, salary_min, salary_max, currency = _parse_title(
        "Middle Laravel Developer в BRAZY, $2500–2700, віддалено"
    )

    assert salary_min == 2500
    assert salary_max == 2700
    assert currency == "USD"


def test_parse_title_joins_multiple_cities():
    _, _, location, _, _, _, _ = _parse_title(
        "Middle Full Stack Engineer в Agiliway, Львів, Чернівці, віддалено"
    )

    assert location == "Львів, Чернівці"


def test_parse_title_uses_the_last_separator_when_the_title_itself_contains_one():
    """Regression: an independent Codex review found a real DOU posting
    whose own title contains " в " ("Treasury Specialist / Financial
    Specialist в Glovo" - confirmed live via the vacancy's own <h1>),
    which DOU's feed template then appends its own " в Glovo, Київ" to.
    Splitting on the FIRST " в " would cut the real title in half; the
    template-inserted separator is always the LAST one."""
    title, company, location, _, _, _, _ = _parse_title(
        "Treasury Specialist / Financial Specialist в Glovo в Glovo, Київ"
    )

    assert title == "Treasury Specialist / Financial Specialist в Glovo"
    assert company == "Glovo"
    assert location == "Київ"


def test_parse_title_handles_two_separators_with_a_meaningful_second_title_part():
    """Same shape as the Glovo case but with real, distinct trailing
    words in the title portion rather than a repeated company name."""
    title, company, _, _, _, _, _ = _parse_title(
        "Head of legal в AI-сферу в Artellence, Київ, віддалено"
    )

    assert title == "Head of legal в AI-сферу"
    assert company == "Artellence"


def test_parse_title_extracts_an_exact_single_salary_figure():
    """Regression: an independent Codex review found the original salary
    regex only handled a "$X–Y" range, silently dropping DOU's other real
    formats - confirmed live across multiple categories."""
    _, _, location, _, salary_min, salary_max, currency = _parse_title(
        "Інженер-розробник .NET в Центр Масштабування, $700"
    )

    assert salary_min == 700
    assert salary_max == 700
    assert currency == "USD"
    assert "$700" not in location


def test_parse_title_extracts_an_open_ended_lower_bound():
    """"від $X" ("from $X") - a lower bound with no stated upper limit,
    confirmed live. max must stay None, not silently equal min - a
    "starting from" figure isn't the same claim as an exact salary."""
    _, _, _, _, salary_min, salary_max, currency = _parse_title(
        "Developer в Engineering Club, від $1800, віддалено"
    )

    assert salary_min == 1800
    assert salary_max is None
    assert currency == "USD"


def test_parse_title_extracts_an_open_ended_upper_bound():
    """"до $X" ("up to $X") - confirmed live, real non-breaking space
    (\\xa0) between the word and the amount in DOU's actual markup, not
    a regular space - the regex must match that too, not just ASCII
    whitespace."""
    _, _, _, _, salary_min, salary_max, currency = _parse_title(
        "Senior Engineer в YozmaTech, до\xa0$6500, віддалено"
    )

    assert salary_min is None
    assert salary_max == 6500
    assert currency == "USD"


def test_parse_title_swaps_a_reversed_salary_range():
    """DOU doesn't validate this server-side - a real range where the
    stated "low" figure is actually higher than the "high" one must not
    be stored backwards."""
    _, _, _, _, salary_min, salary_max, _ = _parse_title("Role в Company, $3000–1000, віддалено")

    assert salary_min == 1000
    assert salary_max == 3000


def test_parse_title_handles_company_with_no_trailing_segments():
    title, company, location, remote, _, _, _ = _parse_title("Head of Engineering в Futurra Group")

    assert title == "Head of Engineering"
    assert company == "Futurra Group"
    assert location == ""
    assert remote is False


def test_search_resolves_html_escaped_entities_in_title(monkeypatch):
    """Regression: confirmed live that DOU's feed title text survives
    ElementTree's own XML-level decode still containing "&amp;" for a
    literal "&" (e.g. the raw feed's "R&amp;amp;D" decodes, via normal
    XML parsing, to "R&amp;D" - not yet "R&D"). Title text never passes
    through strip_html()'s own unescape step (only description does), so
    the client has to resolve this itself or every title with an
    ampersand ends up with a literal "&amp;" in stored data."""
    item = ITEM_TEMPLATE.format(
        title="Lead Backend PHP Developer (R&amp;amp;D) в Nova Digital, віддалено",
        link="https://jobs.dou.ua/companies/nova-digital/vacancies/1/",
        description="",
        pubdate="Wed, 19 Aug 2026 13:39:52 +0300",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_rss([item]).encode())

    _patch_httpx_client(monkeypatch, handler)
    jobs = DouClient().search([], "")

    assert jobs[0].title == "Lead Backend PHP Developer (R&D)"
    assert jobs[0].company == "Nova Digital"

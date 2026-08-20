import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dou_client import Job as DouJob  # noqa: E402

from src.ingestion.sources.dou import DouSource, _strip_footer  # noqa: E402


def test_strip_footer_removes_dou_apply_link_text():
    """DOU appends this to every description itself - not written by the
    employer. Confirmed live against a real fetch of the PHP category
    feed: 25/25 postings end in exactly this phrase after HTML-stripping."""
    assert _strip_footer("Real job content here. Відгукнутись на вакансію") == "Real job content here."


def test_strip_footer_does_not_touch_text_without_the_footer():
    text = "Real job content that happens to mention вакансію in passing."
    assert _strip_footer(text) == text


def test_to_vacancy_preserves_entity_escaped_angle_brackets_in_description():
    """Regression: an independent Codex review found the original test
    coverage for this exercised strip_html() directly with an explicit
    unescape_first=False, which would keep passing even if someone
    dropped that argument from DouSource._to_vacancy() itself - the
    actual regression (a real DOU posting about C# generics: "Span&lt;
    T&gt;" surviving ElementTree's decode as literal escaped text getting
    turned into an actual "<T>" tag and stripped) would come back
    unnoticed. This goes through the real adapter method instead."""
    job = DouJob(
        external_id="1",
        title="Senior .NET Developer",
        company="Acme",
        location="",
        remote=True,
        url="https://jobs.dou.ua/companies/acme/vacancies/1/",
        description="Uses Span&lt;T&gt; and Memory&lt;T&gt;.<br>Відгукнутись на вакансію",
    )

    vacancy = DouSource._to_vacancy(job)

    assert "Span<T>" in vacancy.description
    assert "Memory<T>" in vacancy.description


def test_to_vacancy_sets_salary_period_for_an_upper_bound_only_salary():
    """Regression: an independent Codex review found salary_period was
    only set when salary_min was present, silently leaving it blank for
    DOU's "до $X" ("up to $X") upper-bound-only figures even though
    salary_max was correctly parsed."""
    job = DouJob(
        external_id="1", title="Role", company="Acme", location="", remote=True,
        url="https://jobs.dou.ua/companies/acme/vacancies/1/", description="",
        salary_min=None, salary_max=6500.0, salary_currency="USD",
    )

    vacancy = DouSource._to_vacancy(job)

    assert vacancy.salary_max == 6500.0
    assert vacancy.salary_period == "month"

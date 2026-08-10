import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.salary import monthly_salary  # noqa: E402


def test_monthly_period_passes_through_unchanged():
    assert monthly_salary(5000, 8000, "month") == (5000, 8000)


def test_unspecified_period_passes_through_unchanged():
    assert monthly_salary(5000, 8000, "") == (5000, 8000)


def test_year_period_divides_by_twelve():
    lo, hi = monthly_salary(60000, 96000, "year")
    assert lo == 5000
    assert hi == 8000


def test_hour_period_uses_5_5_hours_times_21_days():
    lo, hi = monthly_salary(50, 80, "hour")
    assert lo == 50 * 5.5 * 21
    assert hi == 80 * 5.5 * 21


def test_missing_min_or_max_returns_none():
    assert monthly_salary(None, 8000, "month") is None
    assert monthly_salary(5000, None, "month") is None
    assert monthly_salary(None, None, "") is None

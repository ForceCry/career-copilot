import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingestion.sources.arbeitnow import _strip_footer  # noqa: E402


def test_strip_footer_removes_arbeitnow_platform_footer():
    """Arbeitnow appends this footer to every description itself - not
    written by the employer. Confirmed live against all 192 real
    ingested Arbeitnow postings: 100% end in one of exactly these 4
    variants (country name varies)."""
    for footer in [
        "Find Jobs in Germany on Arbeitnow",
        "Find Jobs in United Kingdom on Arbeitnow",
        "Find more English Speaking Jobs in Germany on Arbeitnow",
        "Find more English Speaking Jobs in United Kingdom on Arbeitnow",
    ]:
        assert _strip_footer(f"Real job content here. {footer}") == "Real job content here."


def test_strip_footer_does_not_touch_text_without_a_footer():
    text = "Real job content that just happens to mention jobs and Arbeitnow in passing."
    assert _strip_footer(text) == text

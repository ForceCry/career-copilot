import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.observability import JsonFormatter  # noqa: E402


def _make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="test.logger", level=logging.INFO, pathname=__file__, lineno=1,
        msg="something happened", args=(), exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formats_as_valid_json_with_expected_fields():
    record = _make_record(source="adzuna", fetched=16)

    parsed = json.loads(JsonFormatter().format(record))

    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test.logger"
    assert parsed["message"] == "something happened"
    assert parsed["source"] == "adzuna"
    assert parsed["fetched"] == 16


def test_httpx_logger_is_raised_to_warning():
    """Regression: turning on root INFO-level logging (configure_logging(),
    triggered by importing src.main, which every test in this suite does
    via conftest.py) made httpx's own per-request logging active for the
    first time - and for the Adzuna source, that log line includes the
    full request URL with app_id/app_key as query params. Confirmed live:
    without this, `docker logs` on a container running scripts/ingest.py
    --source adzuna would contain live API credentials in plain text."""
    assert logging.getLogger("httpx").level == logging.WARNING

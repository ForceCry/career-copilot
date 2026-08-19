import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import ingest  # noqa: E402


def test_record_run_finish_swallows_a_failure_to_persist(caplog):
    """Regression: an independent Codex review found finish_run() was
    called directly inside main()'s except/else blocks - if THAT raised
    (e.g. MySQL briefly unreachable right as the run finishes), the new
    exception would replace whatever main() was actually reporting,
    masking a real ingest failure behind an unrelated DB error, or
    turning a successful ingest into a reported script failure for a
    reason unrelated to ingestion itself. _record_run_finish must never
    let a failure to persist history propagate."""
    with patch("ingest.finish_run", side_effect=RuntimeError("MySQL gone")):
        ingest._record_run_finish("adzuna", 1, fetched_count=5)  # must not raise

    assert "failed to record ingestion_run outcome" in caplog.text


def test_record_run_finish_calls_finish_run_with_given_kwargs():
    with patch("ingest.finish_run") as finish_run:
        ingest._record_run_finish("adzuna", 42, fetched_count=5, new_count=1, updated_count=4)

    args, kwargs = finish_run.call_args
    assert args[1] == 42  # run_id
    assert kwargs == {"fetched_count": 5, "new_count": 1, "updated_count": 4}

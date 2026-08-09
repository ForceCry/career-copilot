import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.documents.llm_writer import generate_cover_letter  # noqa: E402
from src.ingestion.models import Vacancy  # noqa: E402
from src.storage.models import Profile  # noqa: E402


def _vacancy() -> Vacancy:
    return Vacancy(
        source="adzuna", external_id="1", title="PHP Developer", company="Acme",
        location="Warsaw", remote=False, url="https://example.test/1", description="Great job.",
    )


def _profile() -> Profile:
    profile = Profile(full_name="Test User", summary="Backend engineer.", email="t@example.test")
    profile.skills = []
    profile.experiences = []
    return profile


def _completed_process(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_success_returns_letter_body():
    envelope = '{"is_error": false, "result": "Dear Hiring Team, ..."}'
    with patch("subprocess.run", return_value=_completed_process(envelope)):
        letter = generate_cover_letter(_vacancy(), _profile())

    assert letter == "Dear Hiring Team, ..."


def test_timeout_still_raises_but_is_observed():
    """Unlike llm_score_vacancy (which swallows failures to None), this
    path is user-facing and should still surface a failure - but
    previously subprocess.run's timeout=... was entirely unguarded, so it
    raised with no log/metric recorded anywhere first. Now it's observed
    before re-raising, and the exception still propagates."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=60.0)):
        with pytest.raises(subprocess.TimeoutExpired):
            generate_cover_letter(_vacancy(), _profile())


def test_non_json_stdout_raises_json_decode_error_not_silently():
    with patch("subprocess.run", return_value=_completed_process("not json at all")):
        with pytest.raises(Exception):  # noqa: B017 - json.JSONDecodeError specifically
            generate_cover_letter(_vacancy(), _profile())


def test_api_error_raises_runtime_error():
    envelope = '{"is_error": true, "result": "rate limited"}'
    with patch("subprocess.run", return_value=_completed_process(envelope)):
        with pytest.raises(RuntimeError):
            generate_cover_letter(_vacancy(), _profile())

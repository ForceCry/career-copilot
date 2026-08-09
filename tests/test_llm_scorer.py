import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingestion.models import Vacancy  # noqa: E402
from src.matching.llm_scorer import llm_score_vacancy  # noqa: E402
from src.storage.models import Profile  # noqa: E402


def _vacancy() -> Vacancy:
    return Vacancy(
        source="adzuna", external_id="1", title="PHP Developer", company="Acme",
        location="Warsaw", remote=False, url="https://example.test/1", description="Great job.",
    )


def _profile() -> Profile:
    profile = Profile(full_name="Test User", summary="Backend engineer.")
    profile.skills = []
    profile.experiences = []
    return profile


def _completed_process(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def _success_envelope(score: int) -> str:
    payload = {"score": score, "reasoning": "good fit", "concerns": []}
    return json.dumps({"is_error": False, "result": json.dumps(payload)})


def test_success_returns_result_with_vacancy_id():
    with patch("subprocess.run", return_value=_completed_process(_success_envelope(85))):
        result = llm_score_vacancy(42, _vacancy(), _profile())

    assert result is not None
    assert result.vacancy_id == 42
    assert result.score == 85


def test_timeout_returns_none_instead_of_raising():
    """Regression: subprocess.run's timeout=... was previously unguarded -
    a slow `claude` call would raise subprocess.TimeoutExpired uncaught,
    crashing the whole /recommendations request instead of just dropping
    one vacancy from the reranked shortlist."""
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30.0)):
        result = llm_score_vacancy(42, _vacancy(), _profile())

    assert result is None


def test_non_json_stdout_returns_none_instead_of_raising():
    """Regression: `envelope = json.loads(process.stdout)` was previously
    unguarded - if the claude CLI ever produced non-JSON stdout despite
    --output-format json, this raised an uncaught JSONDecodeError."""
    with patch("subprocess.run", return_value=_completed_process("not json at all")):
        result = llm_score_vacancy(42, _vacancy(), _profile())

    assert result is None


def test_nonzero_exit_returns_none():
    with patch("subprocess.run", return_value=_completed_process("", returncode=1)):
        result = llm_score_vacancy(42, _vacancy(), _profile())

    assert result is None


def test_api_error_returns_none():
    envelope = '{"is_error": true, "result": "rate limited"}'
    with patch("subprocess.run", return_value=_completed_process(envelope)):
        result = llm_score_vacancy(42, _vacancy(), _profile())

    assert result is None


def test_success_increments_metric():
    from src.observability import LLM_CALLS

    before = LLM_CALLS.labels(operation="score_vacancy", outcome="success")._value.get()
    with patch("subprocess.run", return_value=_completed_process(_success_envelope(50))):
        llm_score_vacancy(1, _vacancy(), _profile())
    after = LLM_CALLS.labels(operation="score_vacancy", outcome="success")._value.get()

    assert after == before + 1

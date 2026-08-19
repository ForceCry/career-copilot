import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from src.storage.ingestion_run_repo import finish_run, list_runs, start_run  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_start_run_creates_row_with_no_finish_yet(session):
    run = start_run(session, "adzuna", "php,symfony", "Warsaw")

    assert run.id is not None
    assert run.source == "adzuna"
    assert run.keywords == "php,symfony"
    assert run.location == "Warsaw"
    assert run.finished_at is None
    assert run.fetched_count is None
    assert run.error is None


def test_finish_run_records_success_counts(session):
    run = start_run(session, "adzuna", "php", "Warsaw")

    finished = finish_run(session, run.id, fetched_count=42, new_count=5, updated_count=37)

    assert finished.finished_at is not None
    assert finished.fetched_count == 42
    assert finished.new_count == 5
    assert finished.updated_count == 37
    assert finished.error is None


def test_finish_run_records_error(session):
    run = start_run(session, "arbeitnow", "php", "Warsaw")

    finished = finish_run(session, run.id, error="ConnectionError: timed out")

    assert finished.finished_at is not None
    assert finished.error == "ConnectionError: timed out"
    assert finished.fetched_count is None


def test_finish_run_raises_for_unknown_id(session):
    with pytest.raises(ValueError):
        finish_run(session, 999999, fetched_count=1)


def test_list_runs_filters_by_source(session):
    start_run(session, "adzuna", "php", "Warsaw")
    start_run(session, "arbeitnow", "php", "Warsaw")

    adzuna_only = list_runs(session, source="adzuna")

    assert len(adzuna_only) == 1
    assert adzuna_only[0].source == "adzuna"


def test_list_runs_orders_most_recent_first(session):
    first = start_run(session, "adzuna", "php", "Warsaw")
    second = start_run(session, "adzuna", "php", "Warsaw")

    runs = list_runs(session, source="adzuna")

    assert [r.id for r in runs] == [second.id, first.id]


def test_list_runs_respects_limit(session):
    for _ in range(5):
        start_run(session, "adzuna", "php", "Warsaw")

    assert len(list_runs(session, limit=2)) == 2


def test_list_runs_empty_when_none_started(session):
    assert list_runs(session) == []

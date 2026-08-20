import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pytest  # noqa: E402
from cleanup_removed_vacancies import _select_deletion_candidates  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from src.storage.models import Application, GeneratedArtifact, VacancyRecord  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _vacancy(session, **overrides) -> VacancyRecord:
    now = datetime.now(UTC).replace(tzinfo=None)
    defaults = dict(
        source="adzuna", external_id="123", title="PHP Developer", company="Acme",
        url="https://example.test/123", status="removed",
        first_seen_at=now - timedelta(days=120), last_seen_at=now - timedelta(days=100),
    )
    record = VacancyRecord(**{**defaults, **overrides})
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def _cutoff(days=90) -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)


def test_old_removed_vacancy_is_a_deletion_candidate(session):
    v = _vacancy(session)

    to_delete, kept = _select_deletion_candidates(session, _cutoff())

    assert to_delete == [v]
    assert kept == []


def test_active_vacancy_is_never_a_candidate_regardless_of_age(session):
    _vacancy(session, status="active", external_id="active-1")

    to_delete, kept = _select_deletion_candidates(session, _cutoff())

    assert to_delete == []
    assert kept == []


def test_recently_removed_vacancy_is_not_yet_a_candidate(session):
    """Only first_seen_at age gates this, not how long it's been
    "removed" - a vacancy from last week that already crossed the
    missed-run threshold is still too young to physically delete."""
    now = datetime.now(UTC).replace(tzinfo=None)
    _vacancy(session, external_id="young-1", first_seen_at=now - timedelta(days=5))

    to_delete, kept = _select_deletion_candidates(session, _cutoff())

    assert to_delete == []
    assert kept == []


def test_tracked_vacancy_is_kept_not_deleted(session):
    """Regardless of status/age, a vacancy the user ever tracked (has an
    Application row) must never be selected for deletion - both because
    the FK would reject it anyway, and because that's exactly the
    history worth keeping."""
    v = _vacancy(session, external_id="tracked-1")
    session.add(Application(vacancy_id=v.id, status="applied"))
    session.commit()

    to_delete, kept = _select_deletion_candidates(session, _cutoff())

    assert to_delete == []
    assert kept == [v]


def test_vacancy_with_a_generated_artifact_is_kept_not_deleted(session):
    v = _vacancy(session, external_id="artifact-1")
    session.add(
        GeneratedArtifact(
            vacancy_id=v.id, artifact_type="cover_letter", content="Dear hiring manager...",
            vacancy_title=v.title, vacancy_company=v.company, vacancy_url=v.url,
        )
    )
    session.commit()

    to_delete, kept = _select_deletion_candidates(session, _cutoff())

    assert to_delete == []
    assert kept == [v]

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import src.storage.application_repo as application_repo  # noqa: E402
from src.ingestion.models import Vacancy  # noqa: E402
from src.storage.application_repo import (  # noqa: E402
    get_application,
    get_applications_map,
    list_applications,
    set_status,
)
from src.storage.models import Application  # noqa: E402
from src.storage.vacancy_repo import upsert_vacancies  # noqa: E402


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _vacancy(**overrides) -> Vacancy:
    defaults = dict(
        source="adzuna", external_id="123", title="PHP Developer",
        company="Acme", location="Warsaw", remote=False,
        url="https://example.test/123", description="Original description.",
    )
    return Vacancy(**{**defaults, **overrides})


def _seed_vacancy_id(session) -> int:
    _, _, ids = upsert_vacancies(session, [_vacancy()])
    return ids[0]


def test_no_application_until_a_status_is_set(session):
    vacancy_id = _seed_vacancy_id(session)
    assert get_application(session, vacancy_id) is None


def test_set_status_creates_application_and_event(session):
    vacancy_id = _seed_vacancy_id(session)

    application = set_status(session, vacancy_id, "saved")

    assert application.vacancy_id == vacancy_id
    assert application.status == "saved"
    assert len(application.events) == 1
    assert application.events[0].status == "saved"


def test_set_status_updates_in_place_and_appends_event(session):
    """Regression check for the roadmap's 'activity timeline' ask -
    Application always reflects only the current status, but every
    transition is preserved via ApplicationEvent (same append-only
    pattern as ResumeVersion)."""
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "saved")
    set_status(session, vacancy_id, "applied", notes="Applied via referral")
    application = set_status(session, vacancy_id, "interviewing")

    assert application.status == "interviewing"
    # still exactly one Application row, not one per status
    all_applications = list_applications(session)
    assert len(all_applications) == 1

    statuses_in_order = [e.status for e in sorted(application.events, key=lambda e: (e.created_at, e.id))]
    assert statuses_in_order == ["saved", "applied", "interviewing"]


def test_set_status_rejects_unknown_status(session):
    vacancy_id = _seed_vacancy_id(session)
    with pytest.raises(ValueError):
        set_status(session, vacancy_id, "ghosted")


def test_notes_and_follow_up_at_are_preserved_when_not_passed(session):
    """Regression: an independent Codex review found that a status-only
    call (the quick-select on the recommendations page, which never
    touches notes/follow_up_at at all) used to silently blank out both
    fields, because the old code had no way to tell "caller didn't
    mention this field" apart from "caller explicitly cleared it". Not
    passing notes/follow_up_at at all must leave whatever's already
    stored untouched."""
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "applied", notes="applied via referral", follow_up_at=date(2026, 9, 1))

    application = set_status(session, vacancy_id, "interviewing")

    assert application.notes == "applied via referral"
    assert application.follow_up_at == date(2026, 9, 1)


def test_follow_up_at_can_be_explicitly_cleared(session):
    """The other half of the UNSET distinction: passing follow_up_at=None
    explicitly (not just omitting the argument) does clear it - this is
    what the /applications full-edit form does when its date field is
    left blank."""
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "applied", follow_up_at=date(2026, 9, 1))

    application = set_status(session, vacancy_id, "interviewing", follow_up_at=None)

    assert application.follow_up_at is None


def test_list_applications_filters_by_status(session):
    v1 = _seed_vacancy_id(session)
    _, _, v2_ids = upsert_vacancies(session, [_vacancy(external_id="456")])
    v2 = v2_ids[0]
    set_status(session, v1, "saved")
    set_status(session, v2, "rejected")

    saved_only = list_applications(session, statuses=["saved"])
    rejected_only = list_applications(session, statuses=["rejected"])
    both = list_applications(session, statuses=["saved", "rejected"])

    assert [a.vacancy_id for a in saved_only] == [v1]
    assert [a.vacancy_id for a in rejected_only] == [v2]
    assert len(both) == 2


def test_get_applications_map_bulk_lookup(session):
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "dismissed")

    result = get_applications_map(session, [vacancy_id, 999])

    assert set(result.keys()) == {vacancy_id}
    assert result[vacancy_id].status == "dismissed"


def test_get_applications_map_empty_input(session):
    assert get_applications_map(session, []) == {}


def test_set_status_recovers_from_concurrent_create_race(session, monkeypatch):
    """Regression: an independent Codex review found that two concurrent
    first-time status changes on the same vacancy could both see "no
    application yet" (the unique constraint on vacancy_id is only
    enforced at flush, not at the read in set_status), then race to
    INSERT - the loser used to surface a raw IntegrityError/500 instead
    of falling back to updating the row the winner had already committed.
    get_application is mocked to miss exactly once, simulating that race
    window, while the "concurrent" row is genuinely committed first."""
    vacancy_id = _seed_vacancy_id(session)

    real_get_application = application_repo.get_application
    calls = {"n": 0}

    def flaky_get_application(s, v):
        calls["n"] += 1
        if calls["n"] == 1:
            return None
        return real_get_application(s, v)

    monkeypatch.setattr(application_repo, "get_application", flaky_get_application)

    session.add(Application(vacancy_id=vacancy_id, status="saved"))
    session.commit()

    application = application_repo.set_status(session, vacancy_id, "applied")

    assert application.status == "applied"
    assert len(application_repo.list_applications(session)) == 1
    # the "concurrent" row was seeded directly (bypassing set_status), so
    # only the applied transition this call made has an event
    assert [e.status for e in application.events] == ["applied"]


def test_no_event_recorded_when_status_is_unchanged(session):
    """Regression: an independent Codex review found every call appended
    an ApplicationEvent even when the status didn't actually change -
    editing just the notes/follow-up date from /applications produced
    misleading "applied -> applied" entries in what's meant to be a
    transition history, not a general edit log."""
    vacancy_id = _seed_vacancy_id(session)
    set_status(session, vacancy_id, "applied")

    application = set_status(session, vacancy_id, "applied", notes="just editing the note")

    assert application.notes == "just editing the note"
    assert len(application.events) == 1  # not 2

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

from src.ingestion.models import Vacancy  # noqa: E402
from src.storage.models import VacancyRecord  # noqa: E402
from src.storage.vacancy_repo import (  # noqa: E402
    REMOVED_AFTER_MISSED_RUNS,
    STALE_AFTER_MISSED_RUNS,
    get_fresh_vacancy_ids,
    mark_embedding_queued,
    mark_missing_vacancies,
    upsert_vacancies,
)


@pytest.fixture
def session():
    # In-memory SQLite, not the production MySQL connection - the ORM
    # models aren't backend-specific, only storage/db.py's engine builder
    # is, so this is a legitimate way to test upsert_vacancies logic
    # without a live database.
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


def test_new_vacancy_is_queued_for_embedding(session):
    new_count, updated_count, to_embed_ids = upsert_vacancies(session, [_vacancy()])

    assert new_count == 1
    assert updated_count == 0
    assert len(to_embed_ids) == 1


def test_unchanged_reingest_is_not_requeued(session):
    _, _, first_ids = upsert_vacancies(session, [_vacancy()])
    mark_embedding_queued(session, first_ids)  # broker confirmed the publish

    # Same source+external_id, identical title/description - a routine
    # re-ingest of a still-open posting whose embedding was already
    # confirmed queued.
    new_count, updated_count, to_embed_ids = upsert_vacancies(session, [_vacancy()])

    assert new_count == 0
    assert updated_count == 1
    assert to_embed_ids == []


def test_edited_posting_is_requeued_for_embedding(session):
    """Regression: an independent Codex review found that a vacancy whose
    title/description actually changed between ingests got the fresh text
    in MySQL but its Elasticsearch vector stayed permanently stale, since
    only brand-new ids were ever queued for embedding."""
    upsert_vacancies(session, [_vacancy()])

    new_count, updated_count, to_embed_ids = upsert_vacancies(
        session, [_vacancy(title="Senior PHP Developer (updated)")]
    )

    assert new_count == 0
    assert updated_count == 1
    assert len(to_embed_ids) == 1


def test_salary_only_change_does_not_requeue(session):
    """Only title/description drive the embedding text - a salary or
    location edit alone isn't worth an extra TEI/ES round trip."""
    _, _, first_ids = upsert_vacancies(session, [_vacancy()])
    mark_embedding_queued(session, first_ids)

    new_count, updated_count, to_embed_ids = upsert_vacancies(
        session, [_vacancy(location="Krakow")]
    )

    assert updated_count == 1
    assert to_embed_ids == []


def test_never_confirmed_queued_vacancy_is_requeued_on_reingest(session):
    """Regression: an independent Codex review found that a publish which
    silently failed (RabbitMQ down, message unroutable, etc.) left a
    vacancy's embedding forever un-queued, since only genuinely new or
    content-changed ids were ever queued - an unconfirmed one looked
    identical to a confirmed one on the next ingest. embedding_queued_at
    staying NULL is what makes it get picked back up."""
    _, _, first_ids = upsert_vacancies(session, [_vacancy()])
    assert first_ids  # never confirmed - mark_embedding_queued not called

    new_count, updated_count, to_embed_ids = upsert_vacancies(session, [_vacancy()])

    assert new_count == 0
    assert updated_count == 1
    assert to_embed_ids == first_ids


def test_confirmed_then_edited_vacancy_is_requeued_once(session):
    _, _, first_ids = upsert_vacancies(session, [_vacancy()])
    mark_embedding_queued(session, first_ids)

    _, _, to_embed_ids = upsert_vacancies(
        session, [_vacancy(title="Senior PHP Developer (updated)")]
    )

    assert to_embed_ids == first_ids


def test_recently_seen_vacancy_is_fresh(session):
    _, _, ids = upsert_vacancies(session, [_vacancy()])

    fresh = get_fresh_vacancy_ids(session, ids, max_age=timedelta(days=5))

    assert fresh == set(ids)


def test_vacancy_not_reingested_recently_is_not_fresh(session):
    """Regression: a real justjoin.it posting was confirmed 404ing and
    absent from the site's own active-jobs sitemap, yet was still
    ranking as the #1 recommendation days later - vector search has no
    notion of recency at all, and upsert_vacancies only refreshes
    last_seen_at for rows the current ingest batch actually saw, so a
    posting that quietly stopped being returned by its source just
    stops advancing, forever, unless something checks for that."""
    _, _, ids = upsert_vacancies(session, [_vacancy()])
    record = session.get(VacancyRecord, ids[0])
    record.last_seen_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=10)
    session.add(record)
    session.commit()

    fresh = get_fresh_vacancy_ids(session, ids, max_age=timedelta(days=5))

    assert fresh == set()


def test_get_fresh_vacancy_ids_empty_input(session):
    assert get_fresh_vacancy_ids(session, [], max_age=timedelta(days=5)) == set()


def test_new_vacancy_starts_active_with_no_missed_runs(session):
    _, _, ids = upsert_vacancies(session, [_vacancy()])

    record = session.get(VacancyRecord, ids[0])
    assert record.status == "active"
    assert record.missed_run_count == 0


def test_vacancy_absent_from_a_run_increments_its_miss_streak(session):
    _, _, ids = upsert_vacancies(session, [_vacancy()])

    mark_missing_vacancies(session, "adzuna", seen_external_ids=set())

    record = session.get(VacancyRecord, ids[0])
    assert record.missed_run_count == 1
    assert record.status == "active"  # below adzuna's stale threshold


def test_vacancy_reappearing_resets_its_miss_streak(session):
    """A vacancy that missed a few runs (still "active", below the stale
    threshold) but then reappears in a later run must go back to a clean
    slate via upsert_vacancies - not just stop climbing."""
    _, _, ids = upsert_vacancies(session, [_vacancy()])
    for _ in range(3):
        mark_missing_vacancies(session, "adzuna", seen_external_ids=set())
    assert session.get(VacancyRecord, ids[0]).missed_run_count == 3

    upsert_vacancies(session, [_vacancy()])

    record = session.get(VacancyRecord, ids[0])
    assert record.missed_run_count == 0
    assert record.status == "active"


def test_missing_runs_dont_affect_a_different_source(session):
    """mark_missing_vacancies is scoped to one source per call - a
    justjoin.it run finding nothing must never bump an unrelated Adzuna
    vacancy's miss streak just because it also wasn't in that batch."""
    _, _, adzuna_ids = upsert_vacancies(session, [_vacancy(source="adzuna")])

    mark_missing_vacancies(session, "justjoinit", seen_external_ids=set())

    assert session.get(VacancyRecord, adzuna_ids[0]).missed_run_count == 0


def test_vacancy_transitions_to_stale_then_removed_by_threshold(session):
    _, _, ids = upsert_vacancies(session, [_vacancy(source="adzuna")])
    stale_at = STALE_AFTER_MISSED_RUNS["adzuna"]
    removed_at = REMOVED_AFTER_MISSED_RUNS["adzuna"]

    for _ in range(stale_at):
        mark_missing_vacancies(session, "adzuna", seen_external_ids=set())
    assert session.get(VacancyRecord, ids[0]).status == "stale"

    for _ in range(removed_at - stale_at):
        mark_missing_vacancies(session, "adzuna", seen_external_ids=set())
    assert session.get(VacancyRecord, ids[0]).status == "removed"


def test_mark_missing_vacancies_returns_count_of_new_transitions_to_removed(session):
    """Regression-shaped: the return value is purely for the caller's
    logging (see scripts/ingest.py) - it must count vacancies that
    NEWLY crossed into "removed" this call, not every vacancy touched,
    and must not double-count something already removed from a prior
    call."""
    _, _, ids = upsert_vacancies(session, [_vacancy(source="adzuna")])
    removed_at = REMOVED_AFTER_MISSED_RUNS["adzuna"]

    for _ in range(removed_at - 1):
        newly_removed = mark_missing_vacancies(session, "adzuna", seen_external_ids=set())
        assert newly_removed == 0

    newly_removed = mark_missing_vacancies(session, "adzuna", seen_external_ids=set())
    assert newly_removed == 1
    assert session.get(VacancyRecord, ids[0]).status == "removed"

    # Already removed - a further miss shouldn't be recounted.
    newly_removed = mark_missing_vacancies(session, "adzuna", seen_external_ids=set())
    assert newly_removed == 0


def test_a_seen_vacancy_is_excluded_from_missing_by_external_id(session):
    _, _, ids = upsert_vacancies(session, [_vacancy(source="adzuna", external_id="keep-me")])

    mark_missing_vacancies(session, "adzuna", seen_external_ids={"keep-me"})

    record = session.get(VacancyRecord, ids[0])
    assert record.missed_run_count == 0
    assert record.status == "active"


def test_every_registered_source_has_explicit_missed_run_thresholds():
    """Regression: DOU was originally left out of both threshold dicts
    entirely - caught while building Djinni's thresholds and going back
    to check the others, not by a review of DOU itself. Without an entry
    here, a source silently falls back to _status_for_missed_count's
    generic default (1/3), which is only correct for sources checked
    roughly daily - DOU runs hourly, so that default would have marked
    every DOU posting "removed" after 3 missed hourly runs (3 hours)
    instead of the ~3-day window every other source actually gets. This
    doesn't re-verify the specific numbers (see the module-level comment
    for why each one is what it is), just that nothing gets silently
    left on the generic fallback.

    Reads the source list from scripts/ingest.py's own SOURCES registry
    (same import pattern tests/test_ingest_script.py already uses) rather
    than a hardcoded tuple here - flagged by an independent Codex review:
    a hardcoded list would keep passing even if a future new source got
    registered in ingest.py but forgotten in these threshold dicts,
    exactly the gap this test exists to catch."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import ingest

    for source in ingest.SOURCES:
        assert source in STALE_AFTER_MISSED_RUNS, source
        assert source in REMOVED_AFTER_MISSED_RUNS, source

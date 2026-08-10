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
    get_fresh_vacancy_ids,
    mark_embedding_queued,
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

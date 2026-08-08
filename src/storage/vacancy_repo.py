from datetime import UTC, datetime

from sqlmodel import Session, select

from ..ingestion.models import Vacancy as IngestionVacancy
from .models import VacancyRecord


def _utcnow() -> datetime:
    return datetime.now(UTC)


def upsert_vacancies(session: Session, vacancies: list[IngestionVacancy]) -> tuple[int, int, list[int]]:
    """Insert new vacancies, refresh last_seen_at on ones already stored.
    Returns (new_count, updated_count, to_embed_ids) - to_embed_ids is
    what callers should queue for embedding: every genuinely new vacancy,
    plus any existing one whose title/description actually changed.
    Re-embedding on every refresh regardless would be wasted TEI/ES work
    for the common case (re-ingesting a still-open, unedited posting) -
    but skipping re-embeds unconditionally was a real bug, caught by an
    independent Codex review: an edited posting's MySQL row got the new
    text while its Elasticsearch vector stayed permanently stale, since
    nothing would ever re-queue it."""
    now = _utcnow()
    new_count = 0
    updated_count = 0
    new_records: list[VacancyRecord] = []
    changed_ids: list[int] = []

    for v in vacancies:
        existing = session.exec(
            select(VacancyRecord).where(
                VacancyRecord.source == v.source, VacancyRecord.external_id == v.external_id
            )
        ).first()

        if existing:
            content_changed = existing.title != v.title or existing.description != v.description
            never_confirmed_queued = existing.embedding_queued_at is None
            if content_changed or never_confirmed_queued:
                changed_ids.append(existing.id)
            existing.title = v.title
            existing.company = v.company
            existing.location = v.location
            existing.remote = v.remote
            existing.url = v.url
            existing.description = v.description
            existing.tags = ",".join(v.tags)
            existing.posted_at = v.created_at
            existing.salary_min = v.salary_min
            existing.salary_max = v.salary_max
            existing.salary_currency = v.salary_currency
            existing.salary_period = v.salary_period
            existing.salary_is_predicted = v.salary_is_predicted
            existing.last_seen_at = now
            session.add(existing)
            updated_count += 1
        else:
            record = VacancyRecord(
                source=v.source,
                external_id=v.external_id,
                title=v.title,
                company=v.company,
                location=v.location,
                remote=v.remote,
                url=v.url,
                description=v.description,
                tags=",".join(v.tags),
                posted_at=v.created_at,
                salary_min=v.salary_min,
                salary_max=v.salary_max,
                salary_currency=v.salary_currency,
                salary_period=v.salary_period,
                salary_is_predicted=v.salary_is_predicted,
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(record)
            new_records.append(record)
            new_count += 1

    session.flush()  # assigns autoincrement ids to new_records before commit
    to_embed_ids = [r.id for r in new_records] + changed_ids
    session.commit()
    return new_count, updated_count, to_embed_ids


def mark_embedding_queued(session: Session, vacancy_ids: list[int]) -> None:
    """Called only with ids RabbitMQ actually confirmed receiving (see
    messaging.rabbitmq.publish_vacancy_ids) - not with everything
    upsert_vacancies decided should be queued. That distinction is the
    whole point: a partial publish failure leaves exactly the unconfirmed
    ids' embedding_queued_at NULL, so the next ingest run picks them back
    up on its own, without needing a separate outbox table or a manually
    remembered backfill for the common case."""
    if not vacancy_ids:
        return
    now = _utcnow()
    records = session.exec(select(VacancyRecord).where(VacancyRecord.id.in_(vacancy_ids))).all()
    for record in records:
        record.embedding_queued_at = now
        session.add(record)
    session.commit()


def _to_ingestion_vacancy(record: VacancyRecord) -> IngestionVacancy:
    return IngestionVacancy(
        source=record.source,
        external_id=record.external_id,
        title=record.title,
        company=record.company,
        location=record.location,
        remote=record.remote,
        url=record.url,
        description=record.description,
        tags=[t for t in record.tags.split(",") if t],
        created_at=record.posted_at,
        salary_min=record.salary_min,
        salary_max=record.salary_max,
        salary_currency=record.salary_currency,
        salary_period=record.salary_period,
        salary_is_predicted=record.salary_is_predicted,
    )


def get_vacancies_by_ids(session: Session, ids: list[int]) -> dict[int, IngestionVacancy]:
    """Joins vector-search hits (which only carry id/title/company/url,
    see search/es_client.py) back to the full record - description,
    salary, etc. live in MySQL, not duplicated into Elasticsearch."""
    if not ids:
        return {}
    records = session.exec(select(VacancyRecord).where(VacancyRecord.id.in_(ids))).all()
    return {record.id: _to_ingestion_vacancy(record) for record in records}


def query_vacancies(
    session: Session, keywords: list[str], sources: list[str] | None = None
) -> list[IngestionVacancy]:
    """Client-side keyword filtering over whatever's stored, same
    substring-match approach every ingestion source already uses - keeps
    behavior consistent whether a source enforces filtering server-side
    (Adzuna) or not (Arbeitnow, justjoin.it). Location is deliberately not
    filtered again here: it was already applied (or not applicable) at
    ingest time, and re-filtering by a differently-formatted location
    string (e.g. "Warsaw" vs stored "Warszawa, mazowieckie") would just
    silently drop real matches."""
    query = select(VacancyRecord)
    if sources:
        query = query.where(VacancyRecord.source.in_(sources))

    keywords_lower = [k.lower() for k in keywords]
    matched = []
    for record in session.exec(query).all():
        haystack = f"{record.title} {record.description} {record.tags}".lower()
        if not keywords_lower or any(k in haystack for k in keywords_lower):
            matched.append(_to_ingestion_vacancy(record))

    return matched

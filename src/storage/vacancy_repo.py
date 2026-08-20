from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from ..ingestion.models import Vacancy as IngestionVacancy
from .models import VacancyRecord


def _utcnow() -> datetime:
    return datetime.now(UTC)


# Per-source missed-run thresholds for status transitions - a flat count
# would be wildly inconsistent across sources given how different their
# cadences are (Adzuna every ~30min via scripts/ingest.py's cron, so 48
# runs/day; Arbeitnow hourly, 24/day; justjoin.it daily, 1/day). Instead
# each threshold targets roughly the same real-world window regardless of
# source: ~1 day missing -> "stale" (shown, with a badge), ~3 days missing
# -> "removed" (hidden from recommendations). justjoin.it's numbers are a
# bit more conservative than a literal 1x/3x day count (2/5 instead of
# 1/3) since at only one run a day, each individual run is a much weaker
# confirmation - a single missed run there is far more likely to be page-
# ordering/pagination noise than the same miss would be for a source
# that's re-checking every 30-60 minutes.
STALE_AFTER_MISSED_RUNS = {"adzuna": 48, "arbeitnow": 24, "justjoinit": 2}
REMOVED_AFTER_MISSED_RUNS = {"adzuna": 144, "arbeitnow": 72, "justjoinit": 5}


def _status_for_missed_count(source: str, missed_run_count: int) -> str:
    if missed_run_count >= REMOVED_AFTER_MISSED_RUNS.get(source, 3):
        return "removed"
    if missed_run_count >= STALE_AFTER_MISSED_RUNS.get(source, 1):
        return "stale"
    return "active"


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
            existing.status = "active"
            existing.missed_run_count = 0
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


def mark_missing_vacancies(session: Session, source: str, seen_external_ids: set[str]) -> int:
    """Call once per source, only after a run's fetch() actually
    succeeded (never on an errored/partial run - an incomplete fetch
    isn't evidence a vacancy is gone, and would falsely accelerate every
    other still-open posting toward "removed"). Every stored vacancy for
    `source` NOT in `seen_external_ids` gets its miss streak bumped and
    status recomputed from STALE_AFTER_MISSED_RUNS/REMOVED_AFTER_MISSED_
    RUNS; upsert_vacancies already resets the streak to 0 for whatever
    WAS seen, in the same run. Returns how many vacancies transitioned to
    "removed" this call, purely for the caller's own logging - see
    scripts/ingest.py.

    A source that returns zero results on a run it didn't error out of
    (e.g. a temporarily broken query that still comes back HTTP 200) will
    bump every one of that source's vacancies at once - no special-casing
    for "suspiciously empty batch" here, same trust-the-adapter's-own-
    error-handling precedent the rest of ingestion already relies on."""
    rows = session.exec(
        select(VacancyRecord).where(
            VacancyRecord.source == source, VacancyRecord.external_id.not_in(seen_external_ids)
        )
    ).all()

    newly_removed = 0
    for row in rows:
        row.missed_run_count += 1
        new_status = _status_for_missed_count(source, row.missed_run_count)
        if new_status == "removed" and row.status != "removed":
            newly_removed += 1
        row.status = new_status
        session.add(row)

    session.commit()
    return newly_removed


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


def get_fresh_vacancy_ids(session: Session, ids: list[int], max_age: timedelta) -> set[int]:
    """Vector search has no notion of whether a vacancy is still open -
    ES only stores what's needed to rank/label a hit, not last_seen_at.
    Confirmed live: a posting can 404 and drop out of a source's active
    listings entirely while still ranking as a top recommendation,
    because nothing ever re-checks it once it's indexed. upsert_vacancies
    only refreshes last_seen_at for rows the CURRENT ingest batch actually
    saw, so a vacancy that quietly stopped being returned by its source
    just stops advancing - this is what callers filter recommendations
    on to catch that, without needing a separate cleanup job or deleting
    anything."""
    if not ids:
        return set()
    cutoff = datetime.now(UTC).replace(tzinfo=None) - max_age
    rows = session.exec(
        select(VacancyRecord.id).where(VacancyRecord.id.in_(ids), VacancyRecord.last_seen_at >= cutoff)
    ).all()
    return set(rows)


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

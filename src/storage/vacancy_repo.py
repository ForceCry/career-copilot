from datetime import datetime

from sqlmodel import Session, select

from ..ingestion.models import Vacancy as IngestionVacancy
from .models import VacancyRecord


def upsert_vacancies(session: Session, vacancies: list[IngestionVacancy]) -> tuple[int, int]:
    """Insert new vacancies, refresh last_seen_at on ones already stored.
    Returns (new_count, updated_count)."""
    now = datetime.utcnow()
    new_count = 0
    updated_count = 0

    for v in vacancies:
        existing = session.exec(
            select(VacancyRecord).where(
                VacancyRecord.source == v.source, VacancyRecord.external_id == v.external_id
            )
        ).first()

        if existing:
            existing.title = v.title
            existing.company = v.company
            existing.location = v.location
            existing.remote = v.remote
            existing.url = v.url
            existing.description = v.description
            existing.tags = ",".join(v.tags)
            existing.posted_at = v.created_at
            existing.last_seen_at = now
            session.add(existing)
            updated_count += 1
        else:
            session.add(
                VacancyRecord(
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
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            new_count += 1

    session.commit()
    return new_count, updated_count


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
    )


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

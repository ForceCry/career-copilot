from datetime import UTC, datetime

from sqlmodel import Session, select

from .models import IngestionRun


def _utcnow() -> datetime:
    return datetime.now(UTC)


def start_run(session: Session, source: str, keywords: str, location: str) -> IngestionRun:
    run = IngestionRun(source=source, keywords=keywords, location=location)
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def finish_run(
    session: Session,
    run_id: int,
    *,
    fetched_count: int | None = None,
    new_count: int | None = None,
    updated_count: int | None = None,
    error: str | None = None,
) -> IngestionRun:
    run = session.get(IngestionRun, run_id)
    if run is None:
        raise ValueError(f"No IngestionRun with id={run_id!r}")
    run.finished_at = _utcnow()
    run.fetched_count = fetched_count
    run.new_count = new_count
    run.updated_count = updated_count
    run.error = error
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def list_runs(session: Session, source: str | None = None, limit: int = 50) -> list[IngestionRun]:
    query = select(IngestionRun)
    if source:
        query = query.where(IngestionRun.source == source)
    # id, not started_at - MySQL's DATETIME column here has no
    # fractional-second precision (same gotcha already hit with
    # ApplicationEvent/GeneratedArtifact), so two runs started in the
    # same second would otherwise sort arbitrarily. id is monotonic with
    # insertion order regardless.
    query = query.order_by(IngestionRun.id.desc()).limit(limit)
    return session.exec(query).all()

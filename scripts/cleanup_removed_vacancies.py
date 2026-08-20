"""Physically deletes old vacancies confirmed gone from their source.

Scope is deliberately narrow - only rows where BOTH are true:
  - status == "removed" (missed enough consecutive successful ingestion
    runs for its source to be confident it's actually gone - see
    storage/vacancy_repo.py's mark_missing_vacancies/REMOVED_AFTER_
    MISSED_RUNS, not just "hasn't been re-seen in N days")
  - first_seen_at older than --min-age-days (default 90) - a vacancy
    only a few days old that already flipped to "removed" is still left
    alone here; the DB doesn't need trimming for anything this young,
    and it costs nothing to just keep it.

A vacancy the user ever tracked (has an Application row) or generated an
artifact for (cover letter, tailoring suggestions) is never deleted,
regardless of age or status - both have a foreign key on vacancy.id with
no cascade, so MySQL would reject the delete anyway, but this is
filtered out up front rather than relying on that as the only guard:
Applications/GeneratedArtifact are exactly the vacancies where the user
has real history worth keeping around, deletion candidate or not.

Defaults to a dry run - logs what WOULD be deleted without touching
anything. Pass --execute to actually delete (both the MySQL row and its
Elasticsearch document, kept in sync deliberately - an orphaned ES doc
pointing at a gone MySQL row is dead weight, even though
_compute_recommendations already guards against crashing on one).

Run:
  .venv/bin/python scripts/cleanup_removed_vacancies.py               # dry run
  .venv/bin/python scripts/cleanup_removed_vacancies.py --execute
  .venv/bin/python scripts/cleanup_removed_vacancies.py --min-age-days 60 --execute
"""
import argparse
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from elasticsearch import NotFoundError  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from src.observability import configure_logging  # noqa: E402
from src.search.es_client import VACANCY_INDEX, get_client  # noqa: E402
from src.storage.db import engine  # noqa: E402
from src.storage.models import Application, GeneratedArtifact, VacancyRecord  # noqa: E402

logger = logging.getLogger("cleanup_removed_vacancies")


def _select_deletion_candidates(
    session: Session, cutoff: datetime
) -> tuple[list[VacancyRecord], list[VacancyRecord]]:
    """Returns (to_delete, kept_for_history) - split out from main() so
    the selection logic (the part worth having a regression test for) is
    testable against a plain in-memory session, without needing a live
    Elasticsearch to also be reachable just to check who'd be picked."""
    tracked_ids = set(session.exec(select(Application.vacancy_id)).all())
    artifact_ids = set(session.exec(select(GeneratedArtifact.vacancy_id)).all())
    skip_ids = tracked_ids | artifact_ids

    candidates = session.exec(
        select(VacancyRecord).where(
            VacancyRecord.status == "removed", VacancyRecord.first_seen_at < cutoff
        )
    ).all()
    to_delete = [v for v in candidates if v.id not in skip_ids]
    kept_for_history = [v for v in candidates if v.id in skip_ids]
    return to_delete, kept_for_history


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--min-age-days", type=int, default=90)
    parser.add_argument("--execute", action="store_true", help="actually delete (default: dry run)")
    args = parser.parse_args()

    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=args.min_age_days)

    with Session(engine) as session:
        to_delete, kept_for_history = _select_deletion_candidates(session, cutoff)

        logger.info(
            "cleanup scope",
            extra={
                "min_age_days": args.min_age_days,
                "candidates": len(to_delete) + len(kept_for_history),
                "to_delete": len(to_delete),
                "kept_for_application_or_artifact_history": len(kept_for_history),
                "dry_run": not args.execute,
            },
        )

        if not args.execute:
            for v in to_delete:
                logger.info(
                    "would delete",
                    extra={"vacancy_id": v.id, "source": v.source, "title": v.title, "company": v.company},
                )
            return

        client = get_client()
        deleted = 0
        for v in to_delete:
            try:
                client.delete(index=VACANCY_INDEX, id=v.id)
            except NotFoundError:
                pass  # never got indexed, or already gone - fine either way
            session.delete(v)
            deleted += 1

        session.commit()
        logger.info("cleanup finished", extra={"deleted": deleted})


if __name__ == "__main__":
    main()

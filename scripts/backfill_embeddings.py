"""One-off backfill: queue every vacancy currently in MySQL for embedding,
regardless of whether it's already indexed in Elasticsearch.

Mainly useful for a schema/model change that needs everything
re-embedded (e.g. switching embedding models), or a bulk recovery after
an extended RabbitMQ outage. For the routine case - a vacancy that
failed to get confirmed-queued on a normal ingest run - the next
`scripts/ingest.py` run for that source picks it back up on its own
(see storage/vacancy_repo.py's embedding_queued_at tracking), no manual
step needed.

Safe to run anytime: indexing is idempotent (ES doc id = vacancy id, so
re-embedding overwrites rather than duplicates).

Run: .venv/bin/python scripts/backfill_embeddings.py
"""
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from sqlmodel import Session, select  # noqa: E402

from src.messaging.rabbitmq import publish_vacancy_ids  # noqa: E402
from src.observability import configure_logging  # noqa: E402
from src.storage.db import engine  # noqa: E402
from src.storage.models import VacancyRecord  # noqa: E402
from src.storage.vacancy_repo import mark_embedding_queued  # noqa: E402

logger = logging.getLogger("backfill_embeddings")

if __name__ == "__main__":
    configure_logging()
    with Session(engine) as session:
        ids = session.exec(select(VacancyRecord.id)).all()

    confirmed_ids = publish_vacancy_ids(list(ids))

    if confirmed_ids:
        with Session(engine) as session:
            mark_embedding_queued(session, confirmed_ids)

    unconfirmed = len(ids) - len(confirmed_ids)
    logger.info(
        "backfill finished",
        extra={"total": len(ids), "queued": len(confirmed_ids), "unconfirmed": unconfirmed},
    )

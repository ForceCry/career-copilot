"""One-off backfill: queue every vacancy currently in MySQL for embedding,
regardless of whether it's already indexed in Elasticsearch.

Needed because ingest.py only queues genuinely *new* vacancies (see its
docstring) - vacancies ingested before the RabbitMQ wiring existed, or
ones indexed under an old embedding, never get (re-)queued on their own.
Safe to run anytime: indexing is idempotent (ES doc id = vacancy id, so
re-embedding overwrites rather than duplicates).

Run: .venv/bin/python scripts/backfill_embeddings.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from sqlmodel import Session, select  # noqa: E402

from src.messaging.rabbitmq import publish_vacancy_ids  # noqa: E402
from src.storage.db import engine  # noqa: E402
from src.storage.models import VacancyRecord  # noqa: E402

if __name__ == "__main__":
    with Session(engine) as session:
        ids = session.exec(select(VacancyRecord.id)).all()

    publish_vacancy_ids(list(ids))
    print(f"Queued {len(ids)} vacancies for (re-)embedding")

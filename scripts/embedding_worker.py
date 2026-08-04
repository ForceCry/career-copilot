"""Long-running consumer: pulls vacancy ids off vacancy.embed, embeds via
TEI, indexes into Elasticsearch. Its own docker-compose service (same
image as the api container, different command) so it scales/retries
independently of ingestion and the web API.

Run: .venv/bin/python scripts/embedding_worker.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import time

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from prometheus_client import Counter, Histogram, start_http_server  # noqa: E402
from sqlmodel import Session  # noqa: E402

from src.messaging.rabbitmq import VACANCY_EMBED_QUEUE, get_connection  # noqa: E402
from src.search.indexer import index_vacancy  # noqa: E402
from src.storage.db import engine  # noqa: E402

METRICS_PORT = 9100

VACANCIES_PROCESSED = Counter(
    "embedding_worker_vacancies_processed_total",
    "Vacancies consumed from vacancy.embed",
    ["status"],  # "indexed" | "not_found"
)
PROCESSING_DURATION = Histogram(
    "embedding_worker_processing_seconds",
    "Time to fetch text, embed via TEI, and index into Elasticsearch for one vacancy",
)


def main() -> None:
    start_http_server(METRICS_PORT)
    print(f"[embedding-worker] metrics on :{METRICS_PORT}/metrics", flush=True)

    connection = get_connection()
    channel = connection.channel()
    channel.queue_declare(queue=VACANCY_EMBED_QUEUE, durable=True)
    channel.basic_qos(prefetch_count=1)

    def on_message(ch, method, _properties, body):
        vacancy_id = int(body.decode())
        start = time.monotonic()
        with Session(engine) as session:
            found = index_vacancy(session, vacancy_id)
        PROCESSING_DURATION.observe(time.monotonic() - start)
        VACANCIES_PROCESSED.labels(status="indexed" if found else "not_found").inc()

        status = "indexed" if found else "not found, skipped"
        print(f"[embedding-worker] vacancy {vacancy_id}: {status}", flush=True)
        ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=VACANCY_EMBED_QUEUE, on_message_callback=on_message)
    print("[embedding-worker] waiting for messages...", flush=True)
    channel.start_consuming()


if __name__ == "__main__":
    main()

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

import httpx
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from elasticsearch.exceptions import TransportError as ESTransportError  # noqa: E402
from prometheus_client import Counter, Histogram, start_http_server  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlmodel import Session  # noqa: E402

from src.messaging.rabbitmq import VACANCY_EMBED_QUEUE, get_connection  # noqa: E402
from src.search.indexer import index_vacancy  # noqa: E402
from src.storage.db import engine  # noqa: E402

METRICS_PORT = 9100
TRANSIENT_RETRY_BACKOFF_SECONDS = 5.0

# Distinguishes "this dependency is temporarily down" from "this message
# is permanently bad" - an independent Codex review pointed out that the
# original blanket ack-on-any-exception fix (needed to stop a poison
# message from blocking the queue forever) went too far the other way:
# it also acked messages that failed only because TEI/ES/MySQL happened
# to be down at that moment, permanently losing them instead of retrying
# once the dependency recovers.
TRANSIENT_EXCEPTIONS = (httpx.TransportError, ESTransportError, OperationalError)

VACANCIES_PROCESSED = Counter(
    "embedding_worker_vacancies_processed_total",
    "Vacancies consumed from vacancy.embed",
    ["status"],  # "indexed" | "not_found" | "failed" | "retrying"
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
        start = time.monotonic()
        try:
            vacancy_id = int(body.decode())
            with Session(engine) as session:
                found = index_vacancy(session, vacancy_id)
            status = "indexed" if found else "not_found"
            print(f"[embedding-worker] vacancy {vacancy_id}: {status}", flush=True)
            PROCESSING_DURATION.observe(time.monotonic() - start)
            VACANCIES_PROCESSED.labels(status=status).inc()
            ch.basic_ack(delivery_tag=method.delivery_tag)

        except TRANSIENT_EXCEPTIONS as exc:
            # TEI/ES/MySQL being temporarily unreachable isn't the
            # message's fault - nack + requeue so it's retried once the
            # dependency is back, instead of being permanently dropped.
            # A short sleep avoids hammering an already-struggling
            # dependency with an immediate retry loop.
            PROCESSING_DURATION.observe(time.monotonic() - start)
            VACANCIES_PROCESSED.labels(status="retrying").inc()
            print(f"[embedding-worker] transient failure, will retry: {exc!r}", flush=True)
            time.sleep(TRANSIENT_RETRY_BACKOFF_SECONDS)
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)

        except Exception as exc:
            # Genuinely bad data (malformed body, a posting that will
            # never pass validation) - retrying changes nothing, so ack
            # and move on rather than block every message behind it
            # forever under prefetch_count=1.
            PROCESSING_DURATION.observe(time.monotonic() - start)
            VACANCIES_PROCESSED.labels(status="failed").inc()
            print(f"[embedding-worker] message failed, acking and continuing: {exc!r}", flush=True)
            ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=VACANCY_EMBED_QUEUE, on_message_callback=on_message)
    print("[embedding-worker] waiting for messages...", flush=True)
    channel.start_consuming()


if __name__ == "__main__":
    main()

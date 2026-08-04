import os

import pika

VACANCY_EMBED_QUEUE = "vacancy.embed"


def _connection_params() -> pika.ConnectionParameters:
    host = os.environ.get("RABBITMQ_HOST", "rabbitmq")
    port = int(os.environ.get("RABBITMQ_PORT", "5672"))
    user = os.environ.get("RABBITMQ_USER", "guest")
    password = os.environ.get("RABBITMQ_PASSWORD", "guest")
    return pika.ConnectionParameters(
        host=host, port=port, credentials=pika.PlainCredentials(user, password)
    )


def get_connection() -> pika.BlockingConnection:
    return pika.BlockingConnection(_connection_params())


def publish_vacancy_ids(vacancy_ids: list[int]) -> list[int]:
    """Publishes one message per id to VACANCY_EMBED_QUEUE - the
    embedding-worker consumes these and does its own lookup, so the
    message body is deliberately just the id, not the vacancy text.

    Returns the subset actually confirmed by the broker. Without
    confirm_delivery()+mandatory=True, basic_publish() always returns
    None regardless of outcome in pika 1.4.2 (verified live) - a broker-
    side failure would have looked identical to success, flagged by an
    independent Codex review. Confirmed live too: an unroutable publish
    raises pika.exceptions.UnroutableError synchronously, which is what
    actually distinguishes success from failure here.

    Caller (see storage/vacancy_repo.mark_embedding_queued) should only
    mark the returned ids as queued - anything not in this list needs to
    stay eligible for a future ingest or backfill to pick up again."""
    if not vacancy_ids:
        return []

    connection = get_connection()
    confirmed_ids: list[int] = []
    try:
        channel = connection.channel()
        channel.queue_declare(queue=VACANCY_EMBED_QUEUE, durable=True)
        channel.confirm_delivery()

        for vacancy_id in vacancy_ids:
            try:
                channel.basic_publish(
                    exchange="",
                    routing_key=VACANCY_EMBED_QUEUE,
                    body=str(vacancy_id).encode(),
                    properties=pika.BasicProperties(delivery_mode=2),  # persist across broker restarts
                    mandatory=True,
                )
                confirmed_ids.append(vacancy_id)
            except (pika.exceptions.UnroutableError, pika.exceptions.NackError):
                continue
    finally:
        connection.close()

    return confirmed_ids

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


def publish_vacancy_ids(vacancy_ids: list[int]) -> None:
    """Publishes one message per id to VACANCY_EMBED_QUEUE - the
    embedding-worker consumes these and does its own lookup, so the
    message body is deliberately just the id, not the vacancy text."""
    if not vacancy_ids:
        return

    connection = get_connection()
    try:
        channel = connection.channel()
        channel.queue_declare(queue=VACANCY_EMBED_QUEUE, durable=True)
        for vacancy_id in vacancy_ids:
            channel.basic_publish(
                exchange="",
                routing_key=VACANCY_EMBED_QUEUE,
                body=str(vacancy_id).encode(),
                properties=pika.BasicProperties(delivery_mode=2),  # persist across broker restarts
            )
    finally:
        connection.close()

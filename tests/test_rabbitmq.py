import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pika  # noqa: E402

from src.messaging.rabbitmq import publish_vacancy_ids  # noqa: E402


def _fake_connection(fail_ids: set[int]):
    """A stand-in for pika.BlockingConnection: basic_publish raises
    UnroutableError for ids in fail_ids, succeeds (returns None, same as
    real pika) for everything else."""
    channel = MagicMock()

    def basic_publish(*, exchange, routing_key, body, properties, mandatory):
        vacancy_id = int(body.decode())
        if vacancy_id in fail_ids:
            raise pika.exceptions.UnroutableError([])

    channel.basic_publish.side_effect = basic_publish

    connection = MagicMock()
    connection.channel.return_value = channel
    return connection


def test_all_confirmed_when_broker_accepts_every_publish():
    with patch("src.messaging.rabbitmq.get_connection", return_value=_fake_connection(set())):
        confirmed = publish_vacancy_ids([1, 2, 3])

    assert confirmed == [1, 2, 3]


def test_unroutable_publish_is_excluded_not_raised():
    """Regression: an independent Codex review found that basic_publish()
    always returns None in pika 1.4.2 regardless of outcome (verified
    live), so the old code had no way to distinguish a broker-side
    failure from success. publish_vacancy_ids must not propagate
    UnroutableError for one bad message - it should keep publishing the
    rest and simply omit the failed id from the confirmed list."""
    with patch("src.messaging.rabbitmq.get_connection", return_value=_fake_connection({2})):
        confirmed = publish_vacancy_ids([1, 2, 3])

    assert confirmed == [1, 3]


def test_empty_input_does_not_open_a_connection():
    with patch("src.messaging.rabbitmq.get_connection") as get_connection:
        confirmed = publish_vacancy_ids([])

    assert confirmed == []
    get_connection.assert_not_called()

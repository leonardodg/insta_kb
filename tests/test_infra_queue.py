"""Unit tests for infra.queue.queue. A fake pika channel/properties stand in
for RabbitMQ so no broker is needed."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import pika

from infra.queue import queue

# pika's BasicProperties.delivery_mode: 2 means "persistent" (survives a
# broker restart). Named here so the assertion below reads as "persistent",
# not an unexplained 2.
PIKA_PERSISTENT_DELIVERY_MODE = 2


class FakeChannel:
    """Records basic_publish calls; queue_declare returns canned counts."""

    def __init__(self):
        self.published: list[dict[str, Any]] = []
        self._queue_counts: dict[str, tuple[int, int]] = {}

    def basic_publish(
        self, *, exchange: str, routing_key: str, body: bytes, properties: Any
    ) -> None:
        self.published.append(
            {
                "exchange": exchange,
                "routing_key": routing_key,
                "body": body,
                "properties": properties,
            }
        )

    def set_queue_counts(
        self, queue_name: str, message_count: int, consumer_count: int
    ) -> None:
        self._queue_counts[queue_name] = (message_count, consumer_count)

    def queue_declare(
        self,
        *,
        queue: str,
        durable: bool,
        passive: bool = False,
        arguments: dict[str, Any] | None = None,
    ) -> SimpleNamespace:
        message_count, consumer_count = self._queue_counts.get(queue, (0, 0))
        method = SimpleNamespace(
            message_count=message_count, consumer_count=consumer_count
        )
        return SimpleNamespace(method=method)


def _props(attempts: int = 0) -> SimpleNamespace:
    return SimpleNamespace(headers={"attempts": attempts})


def test_parse_message_valid():
    msg = {
        "ig_pk": "1",
        "media_type": "video",
        "url": "https://x",
        "title": "t",
        "owner_username": "u",
        "collection_name": None,
        "status": "queued",
    }
    body = json.dumps(msg).encode("utf-8")
    result = queue.parse_message(body)
    assert result["ok"] is True
    assert result["message"] == msg


def test_parse_message_invalid_json():
    result = queue.parse_message(b"not json")
    assert result["ok"] is False
    assert result["error"] == "invalid json"


def test_parse_message_missing_keys():
    body = json.dumps({"ig_pk": "1"}).encode("utf-8")
    result = queue.parse_message(body)
    assert result["ok"] is False
    assert "missing keys" in result["error"]


def test_parse_message_not_an_object():
    body = json.dumps([1, 2, 3]).encode("utf-8")
    result = queue.parse_message(body)
    assert result["ok"] is False
    assert result["error"] == "message is not an object"


def test_attempts_of_defaults_to_zero():
    some_attempts = 2
    assert queue.attempts_of(SimpleNamespace(headers=None)) == 0
    assert queue.attempts_of(_props(some_attempts)) == some_attempts


def test_publish_sets_delivery_mode_and_zero_attempts():
    ch = FakeChannel()
    queue.publish(ch, {"ig_pk": "1"})
    assert len(ch.published) == 1
    pub = ch.published[0]
    assert pub["routing_key"] == queue.QUEUE
    assert pub["properties"].headers == {"attempts": 0}
    assert pub["properties"].delivery_mode == PIKA_PERSISTENT_DELIVERY_MODE
    assert json.loads(pub["body"]) == {"ig_pk": "1"}


def test_handle_failure_requeues_under_max_attempts():
    ch = FakeChannel()
    result = queue.handle_failure(ch, _props(0), b"{}")
    assert result == "requeue"
    assert ch.published[0]["routing_key"] == queue.QUEUE
    assert ch.published[0]["properties"].headers == {"attempts": 1}


def test_handle_failure_dead_letters_at_max_attempts():
    ch = FakeChannel()
    result = queue.handle_failure(ch, _props(queue.MAX_ATTEMPTS - 1), b"{}")
    assert result == "dead"
    assert ch.published[0]["routing_key"] == queue.DLQ


def test_dead_letter_bumps_attempts_but_always_to_dlq():
    ch = FakeChannel()
    queue.dead_letter(ch, _props(0), b"{}")
    assert ch.published[0]["routing_key"] == queue.DLQ
    assert ch.published[0]["properties"].headers == {"attempts": 1}


def test_queue_status_reports_counts():
    ch = FakeChannel()
    ch.set_queue_counts(queue.QUEUE, message_count=5, consumer_count=1)
    ch.set_queue_counts(queue.DLQ, message_count=2, consumer_count=0)
    status = queue.queue_status(ch)
    assert status == {
        "ok": True,
        "queue": queue.QUEUE,
        "ready": 5,
        "dead": 2,
        "consumers": 1,
    }


def test_close_swallows_errors():
    class BrokenConnection:
        def close(self) -> None:
            raise RuntimeError("already closed")

    # must not raise. cast(): a duck-typed fake standing in for
    # pika.BlockingConnection, not a real one.
    queue.close(cast(pika.BlockingConnection, BrokenConnection()))
    queue.close(None)

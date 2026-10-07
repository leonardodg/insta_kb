"""Unit tests for services.ig_control -- the application-service layer shared
by the REST API and the MCP server (SOLID audit #2: adapters import the
application layer, never each other).

RabbitMQ/Postgres are never touched: `ig_queue`, `db` and `settings` are
monkeypatched with fakes at the module level, mirroring how
tests/test_mcp_server.py covered the same logic before it moved here.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services import ig_control

# Expected values asserted below, named so ruff's PLR2004 does not read them
# as unexplained magic numbers.
QUEUE_READY_COUNT = 3
QUEUE_DEAD_COUNT = 1
QUEUE_CONSUMER_COUNT = 1
DOCUMENTS_WITH_IG_PK = 3
DEFAULT_LAST_N = 10


def _noop_declare(ch: Any) -> None:
    pass


def _noop_close(conn: Any) -> None:
    pass


class FakeConnection:
    def __init__(self, channel: "FakeChannel") -> None:
        self._channel = channel

    def channel(self) -> "FakeChannel":
        return self._channel


class FakeChannel:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def basic_publish(
        self,
        *,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: Any = None,
    ) -> None:
        self.published.append(
            {"exchange": exchange, "routing_key": routing_key, "body": body}
        )

    def queue_declare(
        self,
        *,
        queue: str,
        durable: bool,
        passive: bool = False,
        arguments: dict[str, Any] | None = None,
    ) -> SimpleNamespace:
        counts = {"ig.saved": (3, 1), "ig.saved.dead": (1, 0)}
        message_count, consumer_count = counts.get(queue, (0, 0))
        return SimpleNamespace(
            method=SimpleNamespace(
                message_count=message_count, consumer_count=consumer_count
            )
        )


def test_queue_status_reads_broker_via_queue_module(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_channel = FakeChannel()
    fake_conn = FakeConnection(fake_channel)
    monkeypatch.setattr(ig_control.ig_queue, "connect", lambda: fake_conn)
    monkeypatch.setattr(ig_control.ig_queue, "close", _noop_close)

    result = ig_control.queue_status()
    assert result["ok"] is True
    assert result["ready"] == QUEUE_READY_COUNT
    assert result["dead"] == QUEUE_DEAD_COUNT
    assert result["consumers"] == QUEUE_CONSUMER_COUNT


def test_queue_status_wraps_broker_errors(monkeypatch: pytest.MonkeyPatch):
    def boom() -> Any:
        raise ConnectionError("broker down")

    monkeypatch.setattr(ig_control.ig_queue, "connect", boom)

    result = ig_control.queue_status()
    assert result["ok"] is False
    assert "ig_queue_status failed" in result["error"]


def test_publish_control_command_publishes_to_control_queue(
    monkeypatch: pytest.MonkeyPatch,
):
    fake_channel = FakeChannel()
    fake_conn = FakeConnection(fake_channel)
    monkeypatch.setattr(ig_control.ig_queue, "connect", lambda: fake_conn)
    monkeypatch.setattr(ig_control.ig_queue, "declare", _noop_declare)
    monkeypatch.setattr(ig_control.ig_queue, "close", _noop_close)

    result = ig_control.publish_control_command("start")
    assert result == {"ok": True, "command": "start"}
    assert len(fake_channel.published) == 1
    assert "start" in fake_channel.published[0]["body"]
    assert fake_channel.published[0]["routing_key"] == ig_control.ig_queue.CONTROL_QUEUE


def test_publish_control_command_reports_errors(monkeypatch: pytest.MonkeyPatch):
    def boom() -> Any:
        raise ConnectionError("broker down")

    monkeypatch.setattr(ig_control.ig_queue, "connect", boom)

    result = ig_control.publish_control_command("stop")
    assert result["ok"] is False
    assert "ig_worker_stop failed" in result["error"]


def test_get_progress_reads_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state_file = tmp_path / "state.json"
    done_entries = [
        {"ig_pk": "1", "status": "done"},
        {"ig_pk": "2", "status": "done"},
    ]
    state_file.write_text(json.dumps(done_entries), encoding="utf-8")
    monkeypatch.setattr(ig_control.settings, "IG_STATE_FILE", str(state_file))

    class FakeSession:
        def close(self) -> None:
            pass

    monkeypatch.setattr(ig_control.db, "get_session", FakeSession)

    def _list_ig_pks(session: Any) -> set[str]:
        return {"1", "2", "3"}

    monkeypatch.setattr(ig_control.db, "list_ig_pks", _list_ig_pks)

    result = ig_control.get_progress(last_n=1)
    assert result["ok"] is True
    assert result["last"] == [{"ig_pk": "2", "status": "done"}]
    assert result["documents_with_ig_pk"] == DOCUMENTS_WITH_IG_PK


def test_get_progress_missing_state_file_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    missing_state_file = tmp_path / "missing.json"
    monkeypatch.setattr(ig_control.settings, "IG_STATE_FILE", str(missing_state_file))

    class FakeSession:
        def close(self) -> None:
            pass

    monkeypatch.setattr(ig_control.db, "get_session", FakeSession)

    def _list_ig_pks(session: Any) -> set[str]:
        return set()

    monkeypatch.setattr(ig_control.db, "list_ig_pks", _list_ig_pks)

    result = ig_control.get_progress(last_n=DEFAULT_LAST_N)
    assert result == {"ok": True, "last": [], "documents_with_ig_pk": 0}

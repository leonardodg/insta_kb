"""Unit tests for mcp_server.server tool functions. RabbitMQ/Postgres/
instagrapi/Ollama are never touched: `ig_sync`, `ig_queue`, `db` and
`knowledge` are monkeypatched with fakes/mocks at the module level -- for
the control-plane tools (queue status / worker start-stop / progress) the
patching targets live in `services.ig_control`, where the logic now is.

Each `@mcp.tool()`-decorated function stays a plain Python function in the
module namespace (fastmcp does not rewrap it there), so these tests call it
directly. Every Field-defaulted parameter is passed explicitly: calling the
function outside of the MCP protocol does not resolve pydantic `Field(...)`
sentinels into their defaults.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_server import server
from services import ig_control

# Expected values asserted below, named so ruff's PLR2004 does not read them
# as unexplained magic numbers.
QUEUE_READY_COUNT = 3
QUEUE_DEAD_COUNT = 1
QUEUE_CONSUMER_COUNT = 1
DOCUMENTS_WITH_IG_PK = 3

# ---------------------------------------------------------------------------
# Instagram: ig_sync_saved / ig_queue_status / ig_worker_start / ig_worker_stop
# / ig_get_progress
# ---------------------------------------------------------------------------


def _noop_declare(ch: Any) -> None:
    pass


def _noop_close(conn: Any) -> None:
    pass


def _noop_publish(ch: Any, msg: Any) -> None:
    pass


class FakeConnection:
    def __init__(self, channel: "FakeChannel") -> None:
        self._channel = channel

    def channel(self) -> "FakeChannel":
        return self._channel


class FakeChannel:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []
        self.declared = False

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


def test_ig_sync_saved_without_sessionid_returns_error(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server.ig_sync, "IG_SESSIONID", "")
    result = server.ig_sync_saved(reprocessar=False)
    assert result == {"ok": False, "error": server.ig_sync.SESSIONID_MISSING}


def test_ig_sync_saved_happy_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server.ig_sync, "IG_SESSIONID", "sess123")
    fake_channel = FakeChannel()
    fake_conn = FakeConnection(fake_channel)

    monkeypatch.setattr(server.ig_queue, "connect", lambda: fake_conn)
    monkeypatch.setattr(server.ig_queue, "declare", _noop_declare)
    monkeypatch.setattr(server.ig_queue, "close", _noop_close)
    monkeypatch.setattr(server.ig_queue, "publish", _noop_publish)

    class FakeSession:
        def close(self) -> None:
            pass

    monkeypatch.setattr(server.db, "get_session", FakeSession)

    def _list_ig_pks(session: Any) -> set[str]:
        return {"1", "2"}

    monkeypatch.setattr(server.db, "list_ig_pks", _list_ig_pks)
    monkeypatch.setattr(server.ig_sync, "make_client", object)

    def _sync_saved_posts(
        client: Any,
        *,
        existing_pks: set[str],
        publish_fn: Any,
        progress_fn: Any,
        reprocessar: bool,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "published": 4,
            "skipped_existing": len(existing_pks),
            "descartados": 0,
            "total": 6,
        }

    monkeypatch.setattr(server.ig_sync, "sync_saved_posts", _sync_saved_posts)

    result = server.ig_sync_saved(reprocessar=False)
    assert result == {
        "ok": True,
        "published": 4,
        "skipped_existing": 2,
        "descartados": 0,
        "total": 6,
    }


def test_ig_sync_saved_catches_exceptions(monkeypatch: pytest.MonkeyPatch):
    # `connect()` itself sits outside the try/except (same as the original
    # minimax_mcp.server source this was ported from) -- a connection failure
    # there is NOT caught by this tool. Exercise the try block instead, by
    # failing a step after the connection is established.
    monkeypatch.setattr(server.ig_sync, "IG_SESSIONID", "sess123")
    fake_channel = FakeChannel()
    fake_conn = FakeConnection(fake_channel)
    monkeypatch.setattr(server.ig_queue, "connect", lambda: fake_conn)
    monkeypatch.setattr(server.ig_queue, "declare", _noop_declare)
    monkeypatch.setattr(server.ig_queue, "close", _noop_close)

    def boom() -> Any:
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(server.db, "get_session", boom)
    result = server.ig_sync_saved(reprocessar=False)
    assert result["ok"] is False
    assert "ig_sync_saved failed" in result["error"]


def test_ig_queue_status_happy_path(monkeypatch: pytest.MonkeyPatch):
    fake_channel = FakeChannel()
    fake_conn = FakeConnection(fake_channel)
    monkeypatch.setattr(ig_control.ig_queue, "connect", lambda: fake_conn)
    monkeypatch.setattr(ig_control.ig_queue, "close", _noop_close)

    result = server.ig_queue_status()
    assert result["ok"] is True
    assert result["ready"] == QUEUE_READY_COUNT
    assert result["dead"] == QUEUE_DEAD_COUNT
    assert result["consumers"] == QUEUE_CONSUMER_COUNT


def test_ig_worker_start_publishes_start_command(monkeypatch: pytest.MonkeyPatch):
    fake_channel = FakeChannel()
    fake_conn = FakeConnection(fake_channel)
    monkeypatch.setattr(ig_control.ig_queue, "connect", lambda: fake_conn)
    monkeypatch.setattr(ig_control.ig_queue, "declare", _noop_declare)
    monkeypatch.setattr(ig_control.ig_queue, "close", _noop_close)

    result = server.ig_worker_start()
    assert result == {"ok": True, "command": "start"}
    assert len(fake_channel.published) == 1
    assert "start" in fake_channel.published[0]["body"]
    assert fake_channel.published[0]["routing_key"] == ig_control.ig_queue.CONTROL_QUEUE


def test_ig_worker_stop_publishes_stop_command(monkeypatch: pytest.MonkeyPatch):
    fake_channel = FakeChannel()
    fake_conn = FakeConnection(fake_channel)
    monkeypatch.setattr(ig_control.ig_queue, "connect", lambda: fake_conn)
    monkeypatch.setattr(ig_control.ig_queue, "declare", _noop_declare)
    monkeypatch.setattr(ig_control.ig_queue, "close", _noop_close)

    result = server.ig_worker_stop()
    assert result == {"ok": True, "command": "stop"}


def test_ig_get_progress_reads_state_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
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

    result = server.ig_get_progress(last_n=1)
    assert result["ok"] is True
    assert result["last"] == [{"ig_pk": "2", "status": "done"}]
    assert result["documents_with_ig_pk"] == DOCUMENTS_WITH_IG_PK


def test_ig_get_progress_missing_state_file_returns_empty(
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

    result = server.ig_get_progress(last_n=10)
    assert result == {"ok": True, "last": [], "documents_with_ig_pk": 0}


# ---------------------------------------------------------------------------
# Knowledge base tools
# ---------------------------------------------------------------------------


def test_knowledge_ingest_text_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}

    def fake_ingest_text(
        text: str, *, source_url: str | None, title: str | None, platform: str
    ) -> dict[str, Any]:
        captured.update(
            text=text, source_url=source_url, title=title, platform=platform
        )
        return {"ok": True, "document_id": 42}

    monkeypatch.setattr(server.knowledge, "ingest_text", fake_ingest_text)

    result = server.knowledge_ingest_text(
        text="olá mundo", source_url="https://x", title="t", platform="manual"
    )
    assert result == {"ok": True, "document_id": 42}
    assert captured == {
        "text": "olá mundo",
        "source_url": "https://x",
        "title": "t",
        "platform": "manual",
    }


def test_knowledge_ingest_markdown_delegates(monkeypatch: pytest.MonkeyPatch):
    def _ingest_markdown(
        path: str, *, recursive: bool, doc_type: str
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "path": path,
            "recursive": recursive,
            "doc_type": doc_type,
        }

    monkeypatch.setattr(server.knowledge, "ingest_markdown", _ingest_markdown)
    result = server.knowledge_ingest_markdown(
        path="/tmp/notes", recursive=True, doc_type="document"
    )
    assert result == {
        "ok": True,
        "path": "/tmp/notes",
        "recursive": True,
        "doc_type": "document",
    }


def test_knowledge_ingest_video_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}

    def fake_ingest_video(
        url: str,
        *,
        browser: str,
        downloads_dir: str,
        whisper_model: str,
        whisper_device: str,
    ) -> dict[str, Any]:
        captured.update(
            url=url,
            browser=browser,
            downloads_dir=downloads_dir,
            whisper_model=whisper_model,
            whisper_device=whisper_device,
        )
        return {"ok": True, "document_id": 7}

    monkeypatch.setattr(server.knowledge, "ingest_video", fake_ingest_video)

    result = server.knowledge_ingest_video(
        url="https://instagram.com/p/x", browser="chrome", whisper_model="small"
    )
    assert result == {"ok": True, "document_id": 7}
    assert captured["url"] == "https://instagram.com/p/x"
    assert captured["browser"] == "chrome"
    assert captured["whisper_model"] == "small"


def test_knowledge_ingest_audio_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}

    def fake_ingest_audio(
        path_or_url: str,
        *,
        browser: str,
        downloads_dir: str,
        whisper_model: str,
        whisper_device: str,
    ) -> dict[str, Any]:
        captured.update(path_or_url=path_or_url)
        return {"ok": True, "document_id": 9}

    monkeypatch.setattr(server.knowledge, "ingest_audio", fake_ingest_audio)

    result = server.knowledge_ingest_audio(
        path_or_url="/tmp/a.mp3", browser="chrome", whisper_model="small"
    )
    assert result == {"ok": True, "document_id": 9}
    assert captured["path_or_url"] == "/tmp/a.mp3"


def test_knowledge_search_delegates(monkeypatch: pytest.MonkeyPatch):
    def fake_search(query: str, top_k: int) -> dict[str, Any]:
        return {"ok": True, "query": query, "results": [{"id": 1}][:top_k]}

    monkeypatch.setattr(server.knowledge, "search", fake_search)
    result = server.knowledge_search(query="bitcoin", top_k=5)
    assert result == {"ok": True, "query": "bitcoin", "results": [{"id": 1}]}


def test_knowledge_ask_delegates(monkeypatch: pytest.MonkeyPatch):
    def fake_ask(query: str, top_k: int) -> dict[str, Any]:
        return {"ok": True, "query": query, "answer": "42", "sources": []}

    monkeypatch.setattr(server.knowledge, "ask", fake_ask)
    result = server.knowledge_ask(query="qual a resposta?", top_k=3)
    assert result == {
        "ok": True,
        "query": "qual a resposta?",
        "answer": "42",
        "sources": [],
    }


def test_knowledge_reindex_delegates(monkeypatch: pytest.MonkeyPatch):
    def fake_reindex(embedding_model: str) -> dict[str, Any]:
        return {
            "ok": True,
            "documents_reindexed": 3,
            "model": embedding_model,
        }

    monkeypatch.setattr(server.knowledge, "reindex", fake_reindex)
    result = server.knowledge_reindex(embedding_model="mxbai-embed-large")
    assert result == {
        "ok": True,
        "documents_reindexed": 3,
        "model": "mxbai-embed-large",
    }


def test_kb_list_documents_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}

    def fake_list_documents(
        *,
        limit: int,
        offset: int,
        platform: str | None,
        doc_type: str | None,
        tag: str | None,
    ) -> dict[str, Any]:
        captured.update(
            limit=limit, offset=offset, platform=platform, doc_type=doc_type, tag=tag
        )
        return {
            "ok": True,
            "total": 1,
            "limit": limit,
            "offset": offset,
            "has_more": False,
            "documents": [{"id": 1}],
        }

    monkeypatch.setattr(server.knowledge, "list_documents", fake_list_documents)
    result = server.kb_list_documents(
        limit=20, offset=0, platform="instagram", doc_type="video", tag="turbo"
    )
    assert result["ok"] is True
    assert result["documents"] == [{"id": 1}]
    assert captured == {
        "limit": 20,
        "offset": 0,
        "platform": "instagram",
        "doc_type": "video",
        "tag": "turbo",
    }


def test_kb_export_search_delegates(monkeypatch: pytest.MonkeyPatch):
    def fake_export_search(
        query: str | None, ids: list[int] | None, limit: int
    ) -> dict[str, Any]:
        return {"ok": True, "total": 1, "documents": [{"id": 1}]}

    monkeypatch.setattr(server.knowledge, "export_search", fake_export_search)
    result = server.kb_export_search(query="bitcoin", ids=None, limit=20)
    assert result == {"ok": True, "total": 1, "documents": [{"id": 1}]}


def test_kb_export_delegates(monkeypatch: pytest.MonkeyPatch):
    def fake_export_documents(ids: list[int], output_dir: str) -> dict[str, Any]:
        return {
            "ok": True,
            "output_dir": output_dir,
            "files": [{"id": i} for i in ids],
        }

    monkeypatch.setattr(server.knowledge, "export_documents", fake_export_documents)
    result = server.kb_export(ids=[1, 2], output_dir="output/kb-export/")
    assert result == {
        "ok": True,
        "output_dir": "output/kb-export/",
        "files": [{"id": 1}, {"id": 2}],
    }

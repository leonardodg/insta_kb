"""Unit tests for src/api/main.py. RabbitMQ/Postgres are never touched: the
underlying `mcp_server.server.ig_queue_status`/`ig_get_progress` functions
are monkeypatched directly (same functions the MCP tools call -- the
endpoints under test are thin wrappers around them, by design)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import api.main as api_main

HTTP_OK = 200
QUEUE_READY_COUNT = 3
QUEUE_DEAD_COUNT = 1
QUEUE_CONSUMER_COUNT = 1
DOCUMENTS_WITH_IG_PK = 5
DEFAULT_LAST_N = 10
CUSTOM_LAST_N = 3
DEFAULT_TOP_K_SEARCH = 5
CUSTOM_TOP_K_ASK = 2
DEFAULT_EXPORT_LIMIT = 20
DEFAULT_LIST_LIMIT = 20


def test_healthcheck():
    client = TestClient(api_main.app)
    resp = client.get("/healthcheck")
    assert resp.status_code == HTTP_OK
    body = resp.json()
    assert body["status"] == "healthy"


def test_ig_queue_status_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    def fake_queue_status():
        return {
            "ok": True,
            "queue": "ig.saved",
            "ready": QUEUE_READY_COUNT,
            "dead": QUEUE_DEAD_COUNT,
            "consumers": QUEUE_CONSUMER_COUNT,
        }

    monkeypatch.setattr(api_main.mcp_server, "ig_queue_status", fake_queue_status)
    client = TestClient(api_main.app)
    resp = client.get("/ig/queue-status")
    assert resp.status_code == HTTP_OK
    assert resp.json() == {
        "ok": True,
        "queue": "ig.saved",
        "ready": QUEUE_READY_COUNT,
        "dead": QUEUE_DEAD_COUNT,
        "consumers": QUEUE_CONSUMER_COUNT,
    }


def test_ig_queue_status_endpoint_propagates_error(monkeypatch: pytest.MonkeyPatch):
    def fake_queue_status():
        return {"ok": False, "error": "ig_queue_status failed: broker unreachable"}

    monkeypatch.setattr(api_main.mcp_server, "ig_queue_status", fake_queue_status)
    client = TestClient(api_main.app)
    resp = client.get("/ig/queue-status")
    assert resp.status_code == HTTP_OK
    assert resp.json()["ok"] is False


def test_ig_progress_endpoint_delegates_with_default_last_n(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, int] = {}

    def fake_progress(*, last_n: int) -> dict[str, Any]:
        captured["last_n"] = last_n
        return {"ok": True, "last": [], "documents_with_ig_pk": DOCUMENTS_WITH_IG_PK}

    monkeypatch.setattr(api_main.mcp_server, "ig_get_progress", fake_progress)
    client = TestClient(api_main.app)
    resp = client.get("/ig/progress")
    assert resp.status_code == HTTP_OK
    assert resp.json() == {
        "ok": True,
        "last": [],
        "documents_with_ig_pk": DOCUMENTS_WITH_IG_PK,
    }
    assert captured["last_n"] == DEFAULT_LAST_N


def test_ig_progress_endpoint_forwards_last_n_query_param(
    monkeypatch: pytest.MonkeyPatch,
):
    captured: dict[str, int] = {}

    def fake_progress(*, last_n: int) -> dict[str, Any]:
        captured["last_n"] = last_n
        return {"ok": True, "last": [], "documents_with_ig_pk": 0}

    monkeypatch.setattr(api_main.mcp_server, "ig_get_progress", fake_progress)
    client = TestClient(api_main.app)
    resp = client.get("/ig/progress", params={"last_n": CUSTOM_LAST_N})
    assert resp.status_code == HTTP_OK
    assert captured["last_n"] == CUSTOM_LAST_N


def test_ig_worker_start_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        api_main.mcp_server,
        "ig_worker_start",
        lambda: {"ok": True, "command": "start"},
    )
    client = TestClient(api_main.app)
    resp = client.post("/ig/worker/start")
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True, "command": "start"}


def test_ig_worker_stop_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        api_main.mcp_server, "ig_worker_stop", lambda: {"ok": True, "command": "stop"}
    )
    client = TestClient(api_main.app)
    resp = client.post("/ig/worker/stop")
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True, "command": "stop"}


def test_knowledge_search_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_search(query: str, *, top_k: int) -> dict[str, Any]:
        captured["query"] = query
        captured["top_k"] = top_k
        return {"ok": True, "results": []}

    monkeypatch.setattr(api_main.mcp_server.knowledge, "search", fake_search)
    client = TestClient(api_main.app)
    resp = client.get("/knowledge/search", params={"query": "turbo lora"})
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True, "results": []}
    assert captured["query"] == "turbo lora"
    assert captured["top_k"] == DEFAULT_TOP_K_SEARCH


def test_knowledge_ask_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_ask(query: str, *, top_k: int) -> dict[str, Any]:
        captured["query"] = query
        captured["top_k"] = top_k
        return {"ok": True, "answer": "..."}

    monkeypatch.setattr(api_main.mcp_server.knowledge, "ask", fake_ask)
    client = TestClient(api_main.app)
    resp = client.post(
        "/knowledge/ask", params={"query": "o que é turbo lora?", "top_k": 2}
    )
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True, "answer": "..."}
    assert captured["top_k"] == CUSTOM_TOP_K_ASK


def test_list_documents_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

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
            "documents": [],
        }

    monkeypatch.setattr(
        api_main.mcp_server.knowledge, "list_documents", fake_list_documents
    )
    client = TestClient(api_main.app)
    resp = client.get(
        "/knowledge/documents",
        params={"platform": "instagram", "doc_type": "video", "tag": "turbo"},
    )
    assert resp.status_code == HTTP_OK
    assert resp.json()["total"] == 1
    assert captured == {
        "limit": DEFAULT_LIST_LIMIT,
        "offset": 0,
        "platform": "instagram",
        "doc_type": "video",
        "tag": "turbo",
    }


def test_kb_export_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_kb_export(*, ids: list[int], output_dir: str) -> dict[str, Any]:
        captured["ids"] = ids
        captured["output_dir"] = output_dir
        return {"ok": True, "files": [{"id": i, "ok": True} for i in ids]}

    monkeypatch.setattr(api_main.mcp_server, "kb_export", fake_kb_export)
    client = TestClient(api_main.app)
    resp = client.post("/knowledge/export", params={"ids": [1, 2]})
    assert resp.status_code == HTTP_OK
    assert resp.json()["ok"] is True
    assert captured["ids"] == [1, 2]


def test_kb_export_search_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_export_search(
        *, query: str | None, ids: list[int] | None, limit: int
    ) -> dict[str, Any]:
        captured["query"] = query
        captured["ids"] = ids
        captured["limit"] = limit
        return {"ok": True, "documents": []}

    monkeypatch.setattr(
        api_main.mcp_server.knowledge, "export_search", fake_export_search
    )
    client = TestClient(api_main.app)
    resp = client.get("/knowledge/export/search", params={"query": "turbo"})
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True, "documents": []}
    assert captured["query"] == "turbo"
    assert captured["limit"] == DEFAULT_EXPORT_LIMIT

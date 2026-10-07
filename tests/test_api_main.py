"""Unit tests for src/api/main.py. RabbitMQ/Postgres are never touched: the
underlying service functions (`services.ig_control.*` / `core.knowledge.*`
-- the same ones the MCP tools call) are monkeypatched directly; the
endpoints under test are thin wrappers around them, by design."""

from __future__ import annotations

import ast
from pathlib import Path
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


def test_api_main_does_not_import_mcp_server():
    """SOLID audit #2 (F5): REST and MCP are sibling adapters -- both import
    the application layer (core/services), never each other."""
    api_source = (
        Path(__file__).resolve().parents[1] / "src" / "api" / "main.py"
    ).read_text(encoding="utf-8")
    imported: list[str] = []
    for node in ast.walk(ast.parse(api_source)):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name.split(".")[0] == "mcp_server"]


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

    monkeypatch.setattr(api_main.ig_control, "queue_status", fake_queue_status)
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

    monkeypatch.setattr(api_main.ig_control, "queue_status", fake_queue_status)
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

    monkeypatch.setattr(api_main.ig_control, "get_progress", fake_progress)
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

    monkeypatch.setattr(api_main.ig_control, "get_progress", fake_progress)
    client = TestClient(api_main.app)
    resp = client.get("/ig/progress", params={"last_n": CUSTOM_LAST_N})
    assert resp.status_code == HTTP_OK
    assert captured["last_n"] == CUSTOM_LAST_N


def test_ig_worker_start_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, str] = {}

    def fake_publish(command: str) -> dict[str, Any]:
        captured["command"] = command
        return {"ok": True, "command": command}

    monkeypatch.setattr(api_main.ig_control, "publish_control_command", fake_publish)
    client = TestClient(api_main.app)
    resp = client.post("/ig/worker/start")
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True, "command": "start"}
    assert captured["command"] == "start"


def test_ig_worker_stop_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, str] = {}

    def fake_publish(command: str) -> dict[str, Any]:
        captured["command"] = command
        return {"ok": True, "command": command}

    monkeypatch.setattr(api_main.ig_control, "publish_control_command", fake_publish)
    client = TestClient(api_main.app)
    resp = client.post("/ig/worker/stop")
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True, "command": "stop"}
    assert captured["command"] == "stop"


def test_knowledge_search_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_search(query: str, *, top_k: int) -> dict[str, Any]:
        captured["query"] = query
        captured["top_k"] = top_k
        return {"ok": True, "results": []}

    monkeypatch.setattr(api_main.knowledge, "search", fake_search)
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

    monkeypatch.setattr(api_main.knowledge, "ask", fake_ask)
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

    monkeypatch.setattr(api_main.knowledge, "list_documents", fake_list_documents)
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

    monkeypatch.setattr(api_main.knowledge, "export_documents", fake_kb_export)
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

    monkeypatch.setattr(api_main.knowledge, "export_search", fake_export_search)
    client = TestClient(api_main.app)
    resp = client.get("/knowledge/export/search", params={"query": "turbo"})
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True, "documents": []}
    assert captured["query"] == "turbo"
    assert captured["limit"] == DEFAULT_EXPORT_LIMIT


def test_gpu_status_endpoint_free(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        api_main.gpu_lock, "status", lambda: {"free": True, "holder": None}
    )
    client = TestClient(api_main.app)
    resp = client.get("/gpu/status")
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"free": True, "holder": None}


def test_gpu_acquire_endpoint_success(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_acquire(holder: str, timeout: float) -> str | None:
        captured["holder"] = holder
        captured["timeout"] = timeout
        return "tok-123"

    monkeypatch.setattr(api_main.gpu_lock, "acquire", fake_acquire)
    client = TestClient(api_main.app)
    resp = client.post("/gpu/acquire", json={"holder": "render seed=42", "timeout": 10})
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True, "token": "tok-123", "held_by": None}
    assert captured == {"holder": "render seed=42", "timeout": 10}


def test_gpu_acquire_endpoint_timeout_reports_holder(monkeypatch: pytest.MonkeyPatch):
    def fake_timed_out_acquire(holder: str, timeout: float) -> str | None:
        return None

    def fake_status() -> dict[str, bool | str | None]:
        return {"free": False, "holder": "someone else"}

    monkeypatch.setattr(api_main.gpu_lock, "acquire", fake_timed_out_acquire)
    monkeypatch.setattr(api_main.gpu_lock, "status", fake_status)
    client = TestClient(api_main.app)
    resp = client.post("/gpu/acquire", json={"holder": "me", "timeout": 0.1})
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": False, "token": None, "held_by": "someone else"}


def test_gpu_release_endpoint_delegates(monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, object] = {}

    def fake_release(token: str) -> None:
        captured["token"] = token

    monkeypatch.setattr(api_main.gpu_lock, "release", fake_release)
    client = TestClient(api_main.app)
    resp = client.post("/gpu/release", params={"token": "tok-123"})
    assert resp.status_code == HTTP_OK
    assert resp.json() == {"ok": True}
    assert captured["token"] == "tok-123"

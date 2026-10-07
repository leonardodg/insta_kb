"""Unit tests for core.knowledge.knowledge. Postgres/Ollama are never touched:
db.get_session/save_document/search_documents and llm.generate_structured/
embed/chat are monkeypatched with fakes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from core.knowledge import knowledge, query


class FakeSession:
    def __init__(self) -> None:
        self.closed = False
        self.rolled_back = False

    def close(self) -> None:
        self.closed = True

    def rollback(self) -> None:
        self.rolled_back = True


class FakeDoc:
    def __init__(self, **kw: Any) -> None:
        self.id = 1
        self.title = kw.get("title")
        self.summary = kw.get("summary")
        self.tutorial = kw.get("tutorial")
        self.tags = kw.get("tags")
        self.source_url = kw.get("source_url")
        self.platform = kw.get("platform")
        self.type = kw.get("type")
        self.transcription_text = kw.get("transcription_text")
        self.ig_pk = kw.get("ig_pk")
        self.llm_model = kw.get("llm_model")
        self.objectives = kw.get("objectives")
        self.created_at = kw.get("created_at", "2026-01-01")


def test_ingest_text_empty_text_short_circuits():
    result = knowledge.ingest_text("   ")
    assert result == {"ok": False, "error": "empty text"}


def test_ingest_text_llm_failure_propagates(monkeypatch: pytest.MonkeyPatch):
    def _fail(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {"ok": False, "error": "boom"}

    monkeypatch.setattr(knowledge.llm, "generate_structured", _fail)
    result = knowledge.ingest_text("algum conteúdo")
    assert result == {"ok": False, "stage": "llm", "error": "boom"}


def test_ingest_text_happy_path(monkeypatch: pytest.MonkeyPatch):
    def _generate(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "resumo": "um resumo",
            "tutorial": "um tutorial",
            "objetivos": ["a"],
            "tags": ["t1"],
            "categoria": "outros",
            "provider": "ollama",
            "model": "lfm2:24b",
        }

    monkeypatch.setattr(knowledge.llm, "generate_structured", _generate)

    def _embed(_text: str, **_k: Any) -> list[float]:
        return [0.1, 0.2]

    monkeypatch.setattr(knowledge.llm, "embed", _embed)
    monkeypatch.setattr(knowledge.llm, "EMBEDDING_MODEL", "mxbai-embed-large")

    session = FakeSession()
    monkeypatch.setattr(knowledge.db, "get_session", lambda: session)

    saved_kwargs: dict[str, Any] = {}

    def fake_save_document(sess: Any, **kwargs: Any) -> FakeDoc:
        saved_kwargs.update(kwargs)
        return FakeDoc(
            title=kwargs["title"],
            summary=kwargs["summary"],
            tutorial=kwargs["tutorial"],
            tags=kwargs["tags"],
            source_url=kwargs["source_url"],
            platform=kwargs["platform"],
            type=kwargs["type"],
            transcription_text=kwargs["transcription_text"],
        )

    monkeypatch.setattr(knowledge.db, "save_document", fake_save_document)

    def _write_markdown_copy(doc: Any, path: Any) -> dict[str, Any]:
        return {"ok": True, "skipped": True}

    monkeypatch.setattr(knowledge.vault, "write_markdown_copy", _write_markdown_copy)

    result = knowledge.ingest_text("conteúdo de teste", extra_tags=["colecao:Dev"])
    assert result["ok"] is True
    assert result["document_id"] == 1
    assert "t1" in result["tags"]
    assert "colecao:Dev" in result["tags"]
    assert session.closed is True
    assert saved_kwargs["summary"] == "um resumo"


def test_ingest_text_db_failure_rolls_back(monkeypatch: pytest.MonkeyPatch):
    def _generate(*_a: Any, **_k: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "resumo": "r",
            "tutorial": "",
            "objetivos": [],
            "tags": [],
        }

    monkeypatch.setattr(knowledge.llm, "generate_structured", _generate)
    session = FakeSession()
    monkeypatch.setattr(knowledge.db, "get_session", lambda: session)

    def boom(sess: Any, **kwargs: Any) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(knowledge.db, "save_document", boom)
    result = knowledge.ingest_text("conteúdo")
    assert result["ok"] is False
    assert result["stage"] == "db"
    assert session.rolled_back is True
    assert session.closed is True


def test_search_empty_query():
    assert knowledge.search("  ") == {"ok": False, "error": "empty query"}


def test_search_delegates_to_db(monkeypatch: pytest.MonkeyPatch):
    session = FakeSession()
    monkeypatch.setattr(knowledge.db, "get_session", lambda: session)

    def _search_documents(
        sess: Any, query: str, embed_fn: Any, top_k: int
    ) -> list[dict[str, Any]]:
        return [{"document_id": 1, "snippets": ["x"]}]

    monkeypatch.setattr(knowledge.db, "search_documents", _search_documents)
    result = knowledge.search("python")
    assert result["ok"] is True
    assert result["results"][0]["document_id"] == 1
    assert session.closed is True


def test_search_db_failure_returns_ok_false_not_raise(monkeypatch: pytest.MonkeyPatch):
    # search/ask are the most heavily used read tools -- a transient DB blip
    # must come back as {"ok": False, ...}, not an uncaught exception that
    # propagates as a raw 500/traceback through the MCP tool / REST endpoint.
    session = FakeSession()
    monkeypatch.setattr(knowledge.db, "get_session", lambda: session)

    def boom(sess: Any, query: str, embed_fn: Any, top_k: int) -> Any:
        raise RuntimeError("db down")

    monkeypatch.setattr(knowledge.db, "search_documents", boom)
    result = knowledge.search("python")
    assert result["ok"] is False
    assert "db down" in result["error"]
    assert session.closed is True


def test_ask_no_results_returns_canned_answer(monkeypatch: pytest.MonkeyPatch):
    def _search(query: str, top_k: int = 3) -> dict[str, Any]:
        return {"ok": True, "query": query, "results": []}

    monkeypatch.setattr(query, "search", _search)
    result = knowledge.ask("pergunta qualquer")
    assert result["ok"] is True
    assert result["sources"] == []
    assert "Nada encontrado" in result["answer"]


def test_ask_uses_llm_chat_with_context(monkeypatch: pytest.MonkeyPatch):
    def _search(query: str, top_k: int = 3) -> dict[str, Any]:
        return {
            "ok": True,
            "query": query,
            "results": [
                {
                    "document_id": 1,
                    "title": "Doc",
                    "source_url": "u",
                    "snippets": ["trecho"],
                }
            ],
        }

    monkeypatch.setattr(query, "search", _search)
    captured: dict[str, Any] = {}

    def fake_chat(prompt: str, **k: Any) -> str:
        captured["prompt"] = prompt
        return "resposta final"

    monkeypatch.setattr(knowledge.llm, "chat", fake_chat)
    result = knowledge.ask("o que é X?")
    assert result["ok"] is True
    assert result["answer"] == "resposta final"
    assert result["sources"] == [{"document_id": 1, "title": "Doc", "source_url": "u"}]
    assert "trecho" in captured["prompt"]


def test_export_search_requires_nothing_lists_latest(monkeypatch: pytest.MonkeyPatch):
    session = FakeSession()
    monkeypatch.setattr(knowledge.db, "get_session", lambda: session)

    class FakeExecResult:
        def scalars(self) -> "FakeExecResult":
            return self

        def all(self) -> list[FakeDoc]:
            return [
                FakeDoc(
                    title="A",
                    summary="s",
                    tutorial="t",
                    tags=["x"],
                    transcription_text="z",
                )
            ]

    def _execute(stmt: Any) -> FakeExecResult:
        return FakeExecResult()

    monkeypatch.setattr(session, "execute", _execute, raising=False)
    result = knowledge.export_search()
    assert result["ok"] is True
    assert result["total"] == 1
    assert result["documents"][0]["title"] == "A"


def test_export_documents_requires_ids():
    assert knowledge.export_documents([]) == {"ok": False, "error": "ids required"}


def test_export_documents_rejects_path_traversal_outside_output():
    # output_dir is caller-controlled over the unauthenticated REST API
    # (POST /knowledge/export) -- must not be able to point writes outside
    # {PROJECT_ROOT}/output, no matter how it tries.
    result = knowledge.export_documents([1], output_dir="../../etc")
    assert result["ok"] is False
    assert "output_dir" in result["error"]


def test_export_documents_rejects_absolute_path_outside_output():
    result = knowledge.export_documents([1], output_dir="/etc")
    assert result["ok"] is False
    assert "output_dir" in result["error"]


def test_export_documents_accepts_subdirectory_of_output(
    monkeypatch: pytest.MonkeyPatch,
):
    session = FakeSession()
    monkeypatch.setattr(knowledge.db, "get_session", lambda: session)

    class FakeExecResult:
        def scalars(self) -> "FakeExecResult":
            return self

        def all(self) -> list[FakeDoc]:
            return []

    def _execute(stmt: Any) -> FakeExecResult:
        return FakeExecResult()

    monkeypatch.setattr(session, "execute", _execute, raising=False)
    result = knowledge.export_documents([999], output_dir="output/kb-export/sub")
    assert result["ok"] is True


def test_export_documents_writes_to_the_validated_path_not_cwd(
    monkeypatch: pytest.MonkeyPatch,
):
    # Regression test: an earlier version validated `resolved` (anchored on
    # PROJECT_ROOT) but then called vault.write_markdown_copy(doc, output_dir)
    # with the RAW string -- which vault resolves against the process's CWD,
    # not PROJECT_ROOT. The check and the actual write used two different
    # bases that only agreed by accident (CWD == PROJECT_ROOT in dev/docker).
    # This test forces the FakeDoc to actually be found (so the write call is
    # reached, unlike the accept-path test above) and asserts the exact
    # argument vault.write_markdown_copy receives.
    session = FakeSession()
    monkeypatch.setattr(knowledge.db, "get_session", lambda: session)
    doc = FakeDoc(title="Found doc")

    class FakeExecResult:
        def scalars(self) -> "FakeExecResult":
            return self

        def all(self) -> list[FakeDoc]:
            return [doc]

    def _execute(stmt: Any) -> FakeExecResult:
        return FakeExecResult()

    monkeypatch.setattr(session, "execute", _execute, raising=False)

    captured: dict[str, Any] = {}

    def fake_write(document: dict[str, Any], vault_path: str) -> dict[str, Any]:
        captured["vault_path"] = vault_path
        return {"ok": True, "skipped": False, "path": f"{vault_path}/x.md"}

    monkeypatch.setattr(knowledge.vault, "write_markdown_copy", fake_write)

    result = knowledge.export_documents([1], output_dir="output/kb-export/sub")

    assert result["ok"] is True
    assert result["files"][0]["ok"] is True
    expected_path = Path(knowledge.settings.PROJECT_ROOT) / "output/kb-export/sub"
    expected = str(expected_path.resolve())
    assert captured["vault_path"] == expected
    # The bug this guards against: a raw relative string instead of the
    # PROJECT_ROOT-anchored absolute path.
    assert captured["vault_path"] != "output/kb-export/sub"


def test_export_documents_missing_id_reported_without_aborting(
    monkeypatch: pytest.MonkeyPatch,
):
    session = FakeSession()
    monkeypatch.setattr(knowledge.db, "get_session", lambda: session)

    class FakeExecResult:
        def scalars(self) -> "FakeExecResult":
            return self

        def all(self) -> list[FakeDoc]:
            return []  # nothing found

    def _execute(stmt: Any) -> FakeExecResult:
        return FakeExecResult()

    monkeypatch.setattr(session, "execute", _execute, raising=False)
    result = knowledge.export_documents([999])
    assert result["ok"] is True
    assert result["files"] == [{"id": 999, "ok": False, "error": "not found"}]

"""Unit tests for infra.vault.vault.write_markdown_copy."""

from __future__ import annotations

from pathlib import Path

from infra.vault.vault import write_markdown_copy


def test_write_markdown_copy_skips_without_vault_path():
    result = write_markdown_copy({"title": "x"}, None)
    assert result == {
        "ok": True,
        "skipped": True,
        "reason": "VAULT_PATH not configured",
    }


def test_write_markdown_copy_writes_file(tmp_path: Path):
    doc = {
        "id": 1,
        "title": "Minha Receita",
        "summary": "um resumo",
        "tutorial": "um tutorial",
        "objectives": ["objetivo 1", "objetivo 2"],
        "tags": ["receita", "dica"],
        "source_url": "https://example.com/p/1",
        "platform": "instagram",
        "type": "video",
        "transcription_text": "fala completa",
        "ig_pk": "123",
        "llm_model": "lfm2:24b",
    }
    result = write_markdown_copy(doc, tmp_path)
    assert result["ok"] is True
    assert result["skipped"] is False
    path = result["path"]
    assert path.endswith(".md")
    content = (tmp_path / "Knowledge").glob("*.md")
    files = list(content)
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Minha Receita" in text
    assert "#receita #dica" in text
    assert "- objetivo 1" in text
    assert "fala completa" in text


def test_write_markdown_copy_slug_fallback_for_missing_title(tmp_path: Path):
    result = write_markdown_copy({"id": 42}, tmp_path)
    assert result["ok"] is True
    assert "documento-42" in result["path"]


def test_write_markdown_copy_never_raises_on_bad_path():
    # A path that cannot be created (nested under a filename that is not a
    # directory) must degrade to skipped=True, not raise.
    bad_path = "/dev/null/impossible"
    result = write_markdown_copy({"id": 1, "title": "x"}, bad_path)
    assert result["ok"] is True
    assert result["skipped"] is True
    assert "reason" in result

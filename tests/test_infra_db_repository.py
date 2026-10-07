"""Unit tests for infra.db.repository's pure helpers.

No Postgres required: chunk_text/strip_timestamps/_fuse_rankings are pure
functions. get_engine/get_session/save_document/search_documents all need a
real database and are left to integration tests (out of scope here).
"""

from infra.db.repository import (
    _fuse_rankings,  # pyright: ignore[reportPrivateUsage] -- pure helper, no public wrapper, tested directly on purpose
    chunk_text,
    strip_timestamps,
)


def test_strip_timestamps_removes_whisper_markers():
    text = "[0.00s - 2.30s] olá [2.30s - 5.00s] mundo"
    assert strip_timestamps(text) == "olá mundo"


def test_strip_timestamps_none_passthrough():
    assert strip_timestamps(None) is None
    assert strip_timestamps("") == ""


def test_chunk_text_short_text_single_chunk():
    assert chunk_text("hello world") == ["hello world"]


def test_chunk_text_empty_returns_empty_list():
    assert chunk_text("   ") == []


def test_chunk_text_splits_with_overlap():
    text = "a" * 1500
    chunks = chunk_text(text, max_chars=700, overlap=100)
    assert len(chunks) > 1
    # every char is covered
    assert sum(len(c) for c in chunks) >= len(text)
    # overlap: end of one chunk overlaps start of next
    assert chunks[0][-50:] in text


def test_fuse_rankings_boosts_docs_in_both_lists():
    doc_in_both_lists = 2
    keyword_rows = [(1, "k1"), (doc_in_both_lists, "k2")]
    semantic_rows = [(doc_in_both_lists, "s2"), (3, "s3")]
    fused = _fuse_rankings(keyword_rows, semantic_rows, top_k=5)
    # doc 2 appears in both lists, so it must rank first
    assert fused[0]["document_id"] == doc_in_both_lists
    ids = [r["document_id"] for r in fused]
    assert set(ids) == {1, doc_in_both_lists, 3}


def test_fuse_rankings_respects_top_k():
    top_k = 3
    keyword_rows = [(i, f"k{i}") for i in range(10)]
    fused = _fuse_rankings(keyword_rows, [], top_k=top_k)
    assert len(fused) == top_k

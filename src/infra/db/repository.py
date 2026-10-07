"""Knowledge base storage operations: session/engine, chunking, hybrid search.

Migrated from minimax-video-factory's `minimax_mcp/db.py`. The ORM schema
lives in `infra.db.models`; this module is the session factory plus the pure
text-chunking helpers and the CRUD/search functions that used to share the
file with the schema.

Config change vs. the original: `get_engine()` read `KB_DATABASE_URL` from
`os.environ` directly. Here it reads `settings.DATABASE_URL` (pydantic
Settings, `core/settings/config.py`) instead -- a mechanical swap, same value.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from sqlalchemy import cast, create_engine, delete, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from core.settings.config import settings
from infra.db.models import Chunk, Document, Embedding

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    # `global` (PLW0603) is the deliberate choice here, not an oversight: a
    # SQLAlchemy Engine owns a connection pool, and the whole point of this
    # function is to hand back the SAME one on every call rather than open a
    # fresh pool per call site. A class wrapper would just move the same
    # module-level mutable state one level down for no behavioural gain.
    global _engine  # noqa: PLW0603
    if _engine is None:
        _engine = create_engine(str(settings.DATABASE_URL), future=True)
    return _engine


def get_session() -> Session:
    # Same reasoning as get_engine(): caches the sessionmaker, not sessions
    # (each call still returns a fresh Session).
    global _SessionLocal  # noqa: PLW0603
    if _SessionLocal is None:
        _SessionLocal = sessionmaker[Session](bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


_TIMESTAMP_RE = re.compile(r"\[\s*\d+(?:\.\d+)?s\s*-\s*\d+(?:\.\d+)?s\s*\]\s*")


def strip_timestamps(text: str | None) -> str | None:
    """Drop `[12.34s - 56.78s]` markers from a Whisper transcription.

    The stored `transcription_text` keeps them -- they are how a claim is
    traced back to a moment in the video. But the chunks are what gets
    embedded, and there the markers are pure noise: they carry no meaning,
    they compete for room inside the 700-char budget, and they end up
    matching numeric queries.
    """
    if not text:
        return text
    return _TIMESTAMP_RE.sub("", text).strip()


def chunk_text(text: str, max_chars: int = 700, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks for embedding/search.

    max_chars defaults to 700 so dense content (code/JSON heavy) stays below
    the Ollama embedding batch limit (~512 tokens) even at ~0.75 tokens/char.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    overlap = max(0, min(overlap, max_chars - 1))
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def save_document(
    session: Session,
    *,
    type: str,
    source_url: str | None,
    platform: str | None,
    title: str | None,
    language: str | None,
    transcription_text: str | None,
    summary: str | None,
    tutorial: str | None,
    objectives: str | None,
    tags: list[str] | None,
    raw_file_path: str | None,
    llm_provider: str | None,
    llm_model: str | None,
    embed_fn: Callable[[str], list[float]],
    embedding_model: str,
    ig_pk: str | None = None,
) -> Document:
    """Insert a Document + its chunks + their embeddings in one transaction."""
    doc = Document(
        type=type,
        source_url=source_url,
        platform=platform,
        title=title,
        language=language,
        transcription_text=transcription_text,
        summary=summary,
        tutorial=tutorial,
        objectives=objectives,
        tags=tags,
        raw_file_path=raw_file_path,
        llm_provider=llm_provider,
        llm_model=llm_model,
        ig_pk=ig_pk,
    )
    session.add(doc)
    session.flush()  # assigns doc.id

    text_for_chunks = "\n\n".join(
        filter(None, [summary, tutorial, strip_timestamps(transcription_text)])
    )
    for idx, piece in enumerate(chunk_text(text_for_chunks)):
        chunk = Chunk(document_id=doc.id, chunk_text=piece, chunk_index=idx)
        session.add(chunk)
        session.flush()  # assigns chunk.id
        vector = embed_fn(piece)
        session.add(Embedding(chunk_id=chunk.id, model=embedding_model, vector=vector))

    session.commit()
    session.refresh(doc)
    return doc


def _fuse_rankings(
    keyword_rows: list[tuple[int, str]],
    semantic_rows: list[tuple[int, str]],
    top_k: int,
    rrf_k: float = 60.0,
) -> list[dict[str, Any]]:
    """Fuse keyword (ts_rank) and semantic (cosine) results with Reciprocal
    Rank Fusion (RRF).

    ts_rank scores live in ~0.01-0.1 while cosine similarity is ~0.6-0.9;
    summing them directly lets the semantic branch silently dominate. RRF is
    scale-free: each list contributes 1/(k + rank) per document, so a
    keyword-only match and a semantic-only match at the same rank weigh the
    same, and documents present in both lists get boosted. k=60 is the
    conventional RRF constant.

    Rows are (document_id, snippet) pairs, already ordered by relevance
    (keyword by ts_rank desc, semantic by distance asc). Returns merged docs
    sorted by the fused score, each with its accumulated snippets.
    """
    merged: dict[int, dict[str, Any]] = {}
    for rank, (doc_id, snippet) in enumerate(keyword_rows, start=1):
        entry = merged.setdefault(
            doc_id, {"document_id": doc_id, "snippets": [], "score": 0.0}
        )
        entry["snippets"].append(snippet)
        entry["score"] += 1.0 / (rrf_k + rank)
    for rank, (doc_id, snippet) in enumerate(semantic_rows, start=1):
        entry = merged.setdefault(
            doc_id, {"document_id": doc_id, "snippets": [], "score": 0.0}
        )
        entry["snippets"].append(snippet)
        entry["score"] += 1.0 / (rrf_k + rank)
    return sorted(merged.values(), key=lambda r: r["score"], reverse=True)[:top_k]


def search_documents(
    session: Session,
    query: str,
    embed_fn: Callable[[str], list[float]],
    top_k: int = 5,
) -> list[dict[str, Any]]:
    """Combine Postgres full-text search with pgvector cosine similarity."""
    tsquery = func.plainto_tsquery("portuguese", query)
    keyword_rows = session.execute(
        select(
            Chunk.document_id,
            Chunk.chunk_text,
            func.ts_rank(
                func.to_tsvector("portuguese", Chunk.chunk_text), tsquery
            ).label("score"),
        )
        .where(func.to_tsvector("portuguese", Chunk.chunk_text).op("@@")(tsquery))
        .order_by(
            func.ts_rank(
                func.to_tsvector("portuguese", Chunk.chunk_text), tsquery
            ).desc()
        )
        .limit(top_k)
    ).all()

    query_vector = embed_fn(query)
    semantic_rows = session.execute(
        select(
            Chunk.document_id,
            Chunk.chunk_text,
            Embedding.vector.cosine_distance(query_vector).label("distance"),
        )
        .join(Embedding, Embedding.chunk_id == Chunk.id)
        .order_by(Embedding.vector.cosine_distance(query_vector))
        .limit(top_k)
    ).all()

    ranked = _fuse_rankings(
        keyword_rows=[(r.document_id, r.chunk_text) for r in keyword_rows],
        semantic_rows=[(r.document_id, r.chunk_text) for r in semantic_rows],
        top_k=top_k,
    )
    doc_ids = [r["document_id"] for r in ranked]
    if doc_ids:
        docs_by_id = {
            d.id: d
            for d in session.execute(
                select(Document).where(Document.id.in_(doc_ids))
            ).scalars()
        }
        for r in ranked:
            d = docs_by_id.get(r["document_id"])
            if d is not None:
                r["title"] = d.title
                r["source_url"] = d.source_url
                r["summary"] = d.summary
    return ranked


def reindex_all(
    session: Session, embed_fn: Callable[[str], list[float]], embedding_model: str
) -> int:
    """Recompute chunks + embeddings for every document (e.g. after changing
    EMBEDDING_MODEL)."""
    docs = session.execute(select(Document)).scalars().all()
    for doc in docs:
        session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
        session.flush()
        text_for_chunks = "\n\n".join(
            filter(None, [doc.summary, doc.tutorial, doc.transcription_text])
        )
        for idx, piece in enumerate(chunk_text(text_for_chunks)):
            chunk = Chunk(document_id=doc.id, chunk_text=piece, chunk_index=idx)
            session.add(chunk)
            session.flush()
            vector = embed_fn(piece)
            session.add(
                Embedding(chunk_id=chunk.id, model=embedding_model, vector=vector)
            )
    session.commit()
    return len(docs)


def delete_documents(session: Session, *, source_url: str | None = None) -> int:
    """Delete documents (cascades chunks/embeddings via FK ondelete). Returns
    count.

    Optional source_url filter is useful for idempotent tests.
    """
    stmt = select(Document)
    if source_url is not None:
        stmt = stmt.where(Document.source_url == source_url)
    docs = session.execute(stmt).scalars().all()
    for doc in docs:
        session.delete(doc)
    session.commit()
    return len(docs)


def document_exists(
    session: Session,
    *,
    ig_pk: str | None = None,
    source_url: str | None = None,
) -> bool:
    """True if a document with that ig_pk (or source_url) already exists."""
    if ig_pk is not None:
        stmt = select(Document.id).where(Document.ig_pk == ig_pk)
    elif source_url is not None:
        stmt = select(Document.id).where(Document.source_url == source_url)
    else:
        return False
    return session.execute(stmt.limit(1)).first() is not None


def list_ig_pks(session: Session) -> set[str]:
    """Every non-null ig_pk currently stored (used to skip re-publishing)."""
    rows = session.execute(
        select(Document.ig_pk).where(Document.ig_pk.isnot(None))
    ).all()
    return {pk for (pk,) in rows if pk is not None}


def _apply_catalog_filters(
    stmt: Any,
    *,
    platform: str | None,
    doc_type: str | None,
    tag: str | None,
):
    """Shared WHERE clauses for `count_documents`/`list_documents` -- kept
    in one place so the two can never drift (e.g. count matching more rows
    than the page actually returns).

    `tags` is stored as plain Postgres `json` (not `jsonb`), which has no
    containment operator -- `cast(..., JSONB)` is the standard way to query
    a `json` column as if it were `jsonb` without a migration.
    """
    if platform:
        stmt = stmt.where(Document.platform == platform)
    if doc_type:
        stmt = stmt.where(Document.type == doc_type)
    if tag:
        stmt = stmt.where(cast(Document.tags, JSONB).contains([tag]))
    return stmt


def count_documents(
    session: Session,
    *,
    platform: str | None = None,
    doc_type: str | None = None,
    tag: str | None = None,
) -> int:
    """Total documents matching the filters. Backs pagination for
    `list_documents` -- callers need the full count to compute `has_more`
    without loading every row."""
    stmt = _apply_catalog_filters(
        select(func.count(Document.id)),
        platform=platform,
        doc_type=doc_type,
        tag=tag,
    )
    return session.execute(stmt).scalar_one()


def list_documents(
    session: Session,
    *,
    limit: int = 20,
    offset: int = 0,
    platform: str | None = None,
    doc_type: str | None = None,
    tag: str | None = None,
) -> list[Document]:
    """Paginated catalog of every document, newest first, optionally
    filtered by platform (instagram/youtube/manual/...), doc type
    (video/audio/text/markdown), and/or tag. Used to browse "what's in the
    knowledge base" (e.g. to pick ids for `export_documents`) without
    needing a search query -- `search_documents`/`export_search`'s
    "latest N" mode doesn't paginate past its `limit`."""
    stmt = _apply_catalog_filters(
        select(Document).order_by(Document.created_at.desc()),
        platform=platform,
        doc_type=doc_type,
        tag=tag,
    )
    stmt = stmt.limit(limit).offset(offset)
    return list(session.execute(stmt).scalars().all())

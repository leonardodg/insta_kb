"""Catálogo e exportação: listar documentos e gravar .md legíveis.

Extraído de `core.knowledge.knowledge` (audit F2/SRP, domínio 5 do
facade). Dono de `export_search` (tabela de validação sem escrever nada),
`list_documents` (catálogo paginado com filtros) e `export_documents`
(gravação com a regra anti-traversal: `output_dir` precisa resolver para
dentro de `{PROJECT_ROOT}/output`, e o caminho RESOLVIDO é o que vai para
o vault — validar um path e gravar em outro seria falha de segurança).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import select

from core.settings.config import settings
from infra import db, vault
from infra.llm import client as llm


def _document_to_dict(doc: Any) -> dict[str, Any]:
    """Map a Document ORM row to the dict shape vault.write_markdown_copy
    expects."""
    return {
        "id": doc.id,
        "title": doc.title,
        "summary": doc.summary,
        "tutorial": doc.tutorial,
        "objectives": doc.objectives,
        "tags": doc.tags or [],
        "source_url": doc.source_url,
        "platform": doc.platform,
        "type": doc.type,
        "transcription_text": doc.transcription_text,
        "ig_pk": doc.ig_pk,
        "llm_model": doc.llm_model,
    }


def export_search(
    query: str | None = None,
    ids: list[int] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """List documents for export — by ids, or by keyword query, or latest
    first.

    Never writes anything. Returns the table the user validates before
    calling export_documents.
    """
    session = db.get_session()
    try:
        stmt = select(db.Document)
        if ids:
            stmt = stmt.where(db.Document.id.in_(ids))
        elif query and query.strip():
            ranked = db.search_documents(
                session, query, embed_fn=llm.embed, top_k=limit
            )
            hit_ids = [r["document_id"] for r in ranked]
            if not hit_ids:
                return {"ok": True, "total": 0, "documents": []}
            stmt = (
                select(db.Document)
                .where(db.Document.id.in_(hit_ids))
                .order_by(db.Document.id.desc())
            )
        else:
            stmt = stmt.order_by(db.Document.id.desc())
        stmt = stmt.limit(limit)
        docs = session.execute(stmt).scalars().all()
        documents = [
            {
                "id": d.id,
                "type": d.type,
                "title": d.title,
                "tags": d.tags,
                "ig_pk": d.ig_pk,
                "summary_len": len(d.summary or ""),
                "tutorial_len": len(d.tutorial or ""),
                "transcription_len": len(d.transcription_text or ""),
                "created_at": str(d.created_at),
            }
            for d in docs
        ]
    except Exception as e:
        return {"ok": False, "error": f"export_search failed: {e}"}
    finally:
        session.close()
    return {"ok": True, "total": len(documents), "documents": documents}


def list_documents(
    limit: int = 20,
    offset: int = 0,
    platform: str | None = None,
    doc_type: str | None = None,
    tag: str | None = None,
) -> dict[str, Any]:
    """Paginated catalog of every document in the knowledge base, newest
    first -- browse "what's documented" to pick ids for `export_search`/
    `export_documents`, without needing a search query. `export_search`'s
    "latest N" mode caps at `limit`; this one reports `total` (via
    `count_documents`) so a caller can page through everything. Filter by
    `platform` (instagram/youtube/manual/...), `doc_type` (video/audio/
    text/markdown -- the `type` column), and/or `tag` (exact tag match
    inside the document's tag list).
    """
    session = db.get_session()
    try:
        total = db.count_documents(
            session, platform=platform, doc_type=doc_type, tag=tag
        )
        docs = db.list_documents(
            session,
            limit=limit,
            offset=offset,
            platform=platform,
            doc_type=doc_type,
            tag=tag,
        )
        documents = [
            {
                "id": d.id,
                "type": d.type,
                "title": d.title,
                "platform": d.platform,
                "tags": d.tags,
                "ig_pk": d.ig_pk,
                "summary_len": len(d.summary or ""),
                "tutorial_len": len(d.tutorial or ""),
                "transcription_len": len(d.transcription_text or ""),
                "created_at": str(d.created_at),
            }
            for d in docs
        ]
    except Exception as e:
        return {"ok": False, "error": f"list_documents failed: {e}"}
    finally:
        session.close()
    return {
        "ok": True,
        "total": total,
        "limit": limit,
        "offset": offset,
        "has_more": offset + len(documents) < total,
        "documents": documents,
    }


def export_documents(
    ids: list[int],
    output_dir: str = "output/kb-export/",
) -> dict[str, Any]:
    """Write the selected documents as readable .md files.

    Uses vault.write_markdown_copy, which always appends `/Knowledge` to the
    path — files land under `<output_dir>/Knowledge/` — and never raises. A
    per-file `ok` is True only when the file was actually written; when the
    vault skips (empty output_dir, write error) `ok` is False and `skipped`
    is True. Missing ids are reported per-file without aborting the rest.

    `output_dir` is caller-controlled -- reachable over the unauthenticated
    REST API (`POST /knowledge/export`). Resolved and checked against a
    fixed base before any write, so a caller can't point it at an arbitrary
    path (`../../etc`, an absolute path outside the project, a symlink
    escape) and get this process to write files there. The base is
    `{PROJECT_ROOT}/output` -- same tree the export already defaults into.

    The *resolved, absolute* path is what gets passed to
    vault.write_markdown_copy below -- not the raw `output_dir` string.
    vault.write_markdown_copy joins whatever string it receives as a plain
    `Path(...)`, which resolves relative paths against the process's CWD,
    not PROJECT_ROOT; passing the raw string through would validate one
    path and write to a different one whenever CWD != PROJECT_ROOT.
    """
    if not ids:
        return {"ok": False, "error": "ids required"}
    base = (Path(settings.PROJECT_ROOT) / "output").resolve()
    resolved = (Path(settings.PROJECT_ROOT) / output_dir).resolve()
    if base != resolved and base not in resolved.parents:
        return {"ok": False, "error": f"output_dir must stay under {base}"}
    session = db.get_session()
    try:
        rows = (
            session.execute(select(db.Document).where(db.Document.id.in_(ids)))
            .scalars()
            .all()
        )
        by_id = {d.id: d for d in rows}
    except Exception as e:
        return {"ok": False, "error": f"export_documents failed: {e}"}
    finally:
        session.close()

    files: list[dict[str, Any]] = []
    for doc_id in ids:
        doc = by_id.get(doc_id)
        if doc is None:
            files.append({"id": doc_id, "ok": False, "error": "not found"})
            continue
        result = vault.write_markdown_copy(_document_to_dict(doc), str(resolved))
        written = bool(result.get("ok")) and not result.get("skipped")
        files.append(
            {
                "id": doc_id,
                "ok": written,
                "skipped": result.get("skipped", False),
                "path": result.get("path"),
                "error": result.get("reason"),
            }
        )
    return {"ok": True, "output_dir": output_dir, "files": files}

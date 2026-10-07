"""REST API for the Insta Knowledge Base.

Every endpoint here is a thin wrapper that calls the exact same application
function the equivalent MCP tool calls (`services.ig_control.*` /
`core.knowledge.*`) -- no business logic is duplicated between the MCP and
REST surfaces, and neither adapter imports the other (SOLID audit #2). See
`src/mcp_server/server.py` for the MCP tool docstrings, which describe the
underlying behaviour in full; this module's docstrings focus on the REST
contract (verb, params, response shape) for the auto-generated OpenAPI docs
(Swagger UI at `/docs`, ReDoc at `/redoc` -- FastAPI's built-in, zero-config
documentation, the de-facto standard for FastAPI projects).

Deliberately NOT exposed via REST (MCP-only, see insta_kb's task history for
the reasoning): `ig_sync_saved` (long Instagram scan, rate-limit sensitive),
`knowledge_ingest_*` (synchronous, can take minutes -- needs a background-job
design before it's a good REST citizen), `knowledge_reindex` (heavy,
rebuilds every embedding -- too easy to trigger by accident over HTTP).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Query
from pydantic import BaseModel, Field

from core.knowledge import knowledge
from core.settings.config import settings
from infra import gpu_lock
from services import ig_control

app = FastAPI(
    root_path=settings.PROJECT_ROOT,
    title=settings.APP_NAME,
    description=(
        "Local Instagram & Markdown knowledge base with semantic search and "
        "AI-powered enrichment. Postgres+pgvector for storage, RabbitMQ + a "
        "dedicated ig-worker daemon for the Instagram ingestion pipeline -- "
        "the differentiator against simpler scraping-only knowledge-base "
        "projects: a real queue, a real worker, a real database, not a "
        "one-shot script."
    ),
    version="0.1.0",
)


# =============================================================================
# Response models -- documented schemas for the endpoints that return a
# structured catalog (Swagger/ReDoc render these with field-level docs).
# =============================================================================


class DocumentSummary(BaseModel):
    """One row of the knowledge-base catalog (not the full document body --
    use `knowledge_search`/`knowledge_ask` for content, this is for
    browsing/picking ids to export)."""

    id: int
    type: str
    title: str | None = None
    platform: str | None = Field(
        default=None, description="Origem: instagram, youtube, podcast, manual"
    )
    tags: list[str] | None = None
    ig_pk: str | None = Field(
        default=None, description="ID do post no Instagram, se vier de lá"
    )
    summary_len: int = Field(description="Tamanho do resumo gerado, em caracteres")
    tutorial_len: int
    transcription_len: int
    created_at: str


class DocumentListResponse(BaseModel):
    ok: bool
    total: int = Field(description="Total de documentos que batem com o filtro")
    limit: int
    offset: int
    has_more: bool = Field(description="Se há mais páginas além desta")
    documents: list[DocumentSummary]


class ExportFileResult(BaseModel):
    id: int
    ok: bool
    path: str | None = None
    skipped: bool = False
    error: str | None = None


class ExportResponse(BaseModel):
    ok: bool
    files: list[ExportFileResult] = Field(default_factory=list[ExportFileResult])
    error: str | None = None


@app.get("/healthcheck", tags=["health"], summary="Liveness check")
async def healthcheck() -> dict[str, str]:
    """Returns 200 if the API process is up. Does not check Postgres,
    RabbitMQ, or Ollama -- see `/ig/queue-status` for broker health."""
    return {"app_name": settings.APP_NAME, "status": "healthy"}


# =============================================================================
# Instagram sync pipeline
# =============================================================================


@app.get(
    "/ig/queue-status",
    tags=["instagram"],
    summary="RabbitMQ queue depth and consumer count",
)
async def ig_queue_status_endpoint() -> dict[str, Any]:
    """Mirrors the `ig_queue_status` MCP tool: how many messages are ready
    in `ig.saved`, how many are dead-lettered in `ig.saved.dead`, and how
    many consumers (the ig-worker daemon) are currently attached."""
    return ig_control.queue_status()


@app.get(
    "/ig/progress",
    tags=["instagram"],
    summary="Last N posts processed by the ig-worker",
)
async def ig_progress_endpoint(
    last_n: int = Query(default=10, description="How many recent results to return"),
) -> dict[str, Any]:
    """Mirrors the `ig_get_progress` MCP tool: reads the worker's state
    file plus a count of documents that have an `ig_pk` (i.e. came from
    Instagram, as opposed to manual/markdown ingestion)."""
    return ig_control.get_progress(last_n=last_n)


@app.post(
    "/ig/worker/start",
    tags=["instagram"],
    summary="Resume the ig-worker (consume ig.saved again)",
)
async def ig_worker_start_endpoint() -> dict[str, Any]:
    """Publishes a 'start' control message. Idempotent -- safe to call when
    the worker is already running. Lightweight signal, unlike
    `ig_sync_saved` (not exposed here), so it's safe as a REST action."""
    return ig_control.publish_control_command("start")


@app.post(
    "/ig/worker/stop",
    tags=["instagram"],
    summary="Pause the ig-worker",
)
async def ig_worker_stop_endpoint() -> dict[str, Any]:
    """Publishes a 'stop' control message. Use before a GPU-heavy render
    on a shared card, same reasoning as the CLAUDE.md GPU-contention rule."""
    return ig_control.publish_control_command("stop")


# =============================================================================
# Knowledge base: read
# =============================================================================


@app.get(
    "/knowledge/search",
    tags=["knowledge"],
    summary="Keyword + semantic search over the knowledge base",
)
async def knowledge_search_endpoint(
    query: str = Query(description="Search term or question"),
    top_k: int = Query(default=5, description="Maximum number of results"),
) -> dict[str, Any]:
    """Read-only. Returns the top-k most relevant documents (hybrid
    keyword+embedding ranking, see `infra.db.repository.search_documents`)."""
    return knowledge.search(query, top_k=top_k)


@app.post(
    "/knowledge/ask",
    tags=["knowledge"],
    summary="RAG: answer a question using the knowledge base as context",
)
async def knowledge_ask_endpoint(
    query: str = Query(description="Natural-language question"),
    top_k: int = Query(default=3, description="How many documents to use as context"),
) -> dict[str, Any]:
    """POST (not GET) because it makes an LLM call, not because it mutates
    any state -- same read-only contract as `knowledge_search` underneath."""
    return knowledge.ask(query, top_k=top_k)


@app.get(
    "/knowledge/documents",
    tags=["knowledge"],
    summary="Browse the full document catalog (paginated)",
    response_model=DocumentListResponse,
)
async def list_documents_endpoint(
    limit: int = Query(default=20, ge=1, le=200, description="Page size"),
    offset: int = Query(default=0, ge=0, description="Rows to skip"),
    platform: str | None = Query(
        default=None, description="Filter by source: instagram, youtube, manual, ..."
    ),
    doc_type: str | None = Query(
        default=None,
        description="Filter by category: video, audio, text, markdown",
    ),
    tag: str | None = Query(default=None, description="Filter by one exact tag"),
) -> dict[str, Any]:
    """Lists every document, newest first, independent of any search query
    -- use this to see what's documented before deciding what to export
    with `POST /knowledge/export` (or to pick ids for
    `GET /knowledge/export/search`'s `ids` filter). Supports filtering by
    category (`doc_type`), source (`platform`), and `tag` so a client can
    browse the catalog sliced any of those ways. `total`/`has_more` let a
    client page through the entire filtered set, unlike
    `GET /knowledge/export/search`'s "latest N" mode, which caps at its
    `limit` and reports no total."""
    return knowledge.list_documents(
        limit=limit, offset=offset, platform=platform, doc_type=doc_type, tag=tag
    )


@app.get(
    "/knowledge/export/search",
    tags=["knowledge"],
    summary="Preview which documents an export would include",
)
async def kb_export_search_endpoint(
    query: str | None = Query(
        default=None, description="Keyword/semantic filter (optional)"
    ),
    ids: list[int] | None = Query(default=None, description="Exact document ids"),
    limit: int = Query(default=20, description="Maximum rows returned"),
) -> dict[str, Any]:
    """Read-only; never writes a file. Confirm the list here, then call
    `POST /knowledge/export` with the same `ids` to actually write the
    `.md` files."""
    return knowledge.export_search(query=query, ids=ids, limit=limit)


# =============================================================================
# Knowledge base: write
# =============================================================================


class GpuAcquireRequest(BaseModel):
    holder: str = Field(
        description="Who's asking, e.g. 'ig-worker transcribe post 123'"
    )
    timeout: float = Field(default=300, description="Max seconds to wait for the lock")


class GpuAcquireResponse(BaseModel):
    ok: bool
    token: str | None = Field(
        default=None, description="Pass this to POST /gpu/release when done"
    )
    held_by: str | None = Field(
        default=None, description="Who holds it, if ok=false (timed out)"
    )


class GpuStatusResponse(BaseModel):
    free: bool
    holder: str | None = None


@app.post(
    "/gpu/acquire",
    tags=["gpu"],
    summary="Reserve the shared GPU mutex",
    response_model=GpuAcquireResponse,
)
async def gpu_acquire_endpoint(req: GpuAcquireRequest) -> GpuAcquireResponse:
    """Blocks (server-side) up to `timeout` seconds. Same lock file as
    minimax-video-factory's ComfyUI renders (see infra/gpu_lock/gpu_lock.py
    docstring) -- this machine has one 12 GB GPU, not two. Call before
    Whisper transcription or any other GPU-heavy work; release after."""
    token = gpu_lock.acquire(req.holder, timeout=req.timeout)
    if token is None:
        status = gpu_lock.status()
        return GpuAcquireResponse(ok=False, held_by=status["holder"])
    return GpuAcquireResponse(ok=True, token=token)


@app.post(
    "/gpu/release",
    tags=["gpu"],
    summary="Release a previously acquired GPU mutex",
)
async def gpu_release_endpoint(
    token: str = Query(description="Token from /gpu/acquire"),
) -> dict[str, bool]:
    """Fail-soft: releasing an unknown/already-released token is not an
    error (safe to call unconditionally in a `finally` block)."""
    gpu_lock.release(token)
    return {"ok": True}


@app.get(
    "/gpu/status",
    tags=["gpu"],
    summary="Is the shared GPU mutex free?",
    response_model=GpuStatusResponse,
)
async def gpu_status_endpoint() -> GpuStatusResponse:
    """Read-only, non-blocking. `holder` is a free-text description, not
    guaranteed machine-parseable -- for humans/logs, not for branching
    logic (use /gpu/acquire's timeout for that)."""
    return GpuStatusResponse(**gpu_lock.status())


@app.post(
    "/knowledge/export",
    tags=["knowledge"],
    summary="Export selected documents as readable .md files",
    response_model=ExportResponse,
)
async def kb_export_endpoint(
    ids: list[int] = Query(description="Document ids, confirm first via export/search"),
    output_dir: str = Query(
        default="output/kb-export/",
        description="Destination dir -- files land under '<output_dir>/Knowledge/'",
    ),
) -> dict[str, Any]:
    """Writes one `.md` file per id. Never raises on a single bad id --
    check each entry's `ok`/`skipped`/`error` in the response, the whole
    batch is not rolled back by one failure (same contract as the
    `kb_export` MCP tool / `knowledge.export_documents`)."""
    return knowledge.export_documents(ids=ids, output_dir=output_dir)


# if __name__ == "__main__":
#     uvicorn.run(app, host=settings.API_HOST, port=settings.API_PORT)

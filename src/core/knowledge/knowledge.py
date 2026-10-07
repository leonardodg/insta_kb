"""Fachada da base de conhecimento: a superfície pública que tudo chama.

ESTRUTURA (após o fatiamento do audit F2/SRP): este módulo é só FACHADA —
re-exporta os cinco domínios que antes viviam todos aqui, mantendo
`from core.knowledge import knowledge` e `knowledge.<função>` estáveis
para MCP, API, worker, scripts e testes:

- `core.knowledge.ingest`   — ingest_text / ingest_video / ingest_audio /
  ingest_markdown (LLM → DB → vault) e os helpers de frontmatter;
- `core.knowledge.query`    — search / ask / reindex (RAG);
- `core.knowledge.export`   — export_search / list_documents /
  export_documents (catálogo + anti-traversal).

`llm`, `db`, `vault` e `settings` também são re-exportados de propósito: os testes
fazem `monkeypatch.setattr(knowledge.llm, ...)` nesses objetos de módulo, e
são os MESMOS objetos compartilhados com os submódulos — o patch alcança a
cadeia inteira.

Migrated from minimax-video-factory's `minimax_mcp/knowledge.py`.

Decisions taken during migration that were not 100% specified by the task
and are called out in the migration report:

1. Location: `src/core/knowledge/knowledge.py`, mirroring the existing
   `core/settings` pattern (there was no `use_cases`/`domain` layer to fit
   into, and the task allowed this choice explicitly).
2. Config: `VAULT_PATH` now comes from `core.settings.config.settings`
   instead of `os.environ.get("VAULT_PATH")`.
3. The original `_kb_unavailable()` guard checked
   `os.environ.get("KB_DATABASE_URL")` and refused to run when it was unset
   -- this was how the tool told a caller "you're running the in-container
   MCP variant, which cannot reach Postgres". Under pydantic Settings,
   `settings.DATABASE_URL` always has a value (it's built from defaults),
   so there is no more "unconfigured" state to detect this way. The guard
   is DROPPED here rather than reinvented; a real connection failure now
   surfaces as a normal `stage: "db"` error from `infra.db.get_session()`
   instead of a pre-flight message. Flagged in the report as a behavior
   change, not a silent one.
4. `ingest_video`/`ingest_audio` depend on a `VideoDownloader` class, now
   migrated to `infra.downloader` (closing the gap flagged in the previous
   migration report). The import stays lazy in `core.knowledge.ingest`, as
   in the original `minimax_mcp.knowledge` -- yt-dlp is only needed for the
   URL-download path, not for every caller of this module.
"""

from __future__ import annotations

from core.knowledge.export import export_documents, export_search, list_documents
from core.knowledge.ingest import (
    VAULT_PATH,
    ingest_audio,
    ingest_markdown,
    ingest_text,
    ingest_video,
)
from core.knowledge.query import ask, reindex, search
from core.settings.config import settings
from infra import db, vault
from infra.llm import client as llm

__all__ = [
    "VAULT_PATH",
    "ask",
    "db",
    "export_documents",
    "export_search",
    "ingest_audio",
    "ingest_markdown",
    "ingest_text",
    "ingest_video",
    "list_documents",
    "llm",
    "reindex",
    "search",
    "settings",
    "vault",
]

"""Contratos tipados compartilhados (audit F4: TypedDict/Protocol/draft).

Módulo folha: importa só `typing`, pode ser importado por `core`, `infra`,
`workers`, `api` e `mcp_server` sem ciclagem. Substitui o contrato
universal `dict[str, Any]` (F4a) e formaliza os callables já injetados
(F4b).
"""

from __future__ import annotations

from typing import Any, Literal, NotRequired, Protocol, Required, TypedDict

# ── (a) TypedDicts para os retornos mais indexados por string ──────────────


class OkResult(TypedDict, total=False):
    """Contrato genérico `{"ok": bool, ...}` — o retorno de 90% do repo."""

    ok: Required[bool]
    error: str
    stage: str


class ProcessResult(TypedDict, total=False):
    """Retorno de `workers.ig_worker.process_message` (`res["status"]`)."""

    status: Required[str]
    error: str
    document_id: int | None
    kind: str
    filepath: str
    filepaths: list[str]
    permanent: bool


class DocBuilt(TypedDict):
    """Retorno feliz dos builders `_build_video/_build_image_document`
    (`built["text"]`, `built["categoria"]`). Discriminante `ok: Literal` --
    depois de `if not built["ok"]: return`, pyright afunila para cá."""

    ok: Literal[True]
    text: str
    lang: str
    doc_type: str
    categoria: str | None


class DocBuiltErr(TypedDict, total=False):
    """Ramificação de erro dos builders (mesma chave `ok` como discriminante)."""

    ok: Required[Literal[False]]
    error: str
    permanent: bool
    filepaths: list[str]


class DocumentDraft(TypedDict):
    """Valor de entrada de `db.save_document` — derruba 16 params por
    coluna para ~4 (F4c)."""

    type: str
    source_url: str | None
    platform: str | None
    title: str | None
    language: str | None
    transcription_text: str | None
    summary: str | None
    tutorial: str | None
    objectives: str | None
    tags: list[str] | None
    raw_file_path: str | None
    llm_provider: str | None
    llm_model: str | None
    ig_pk: NotRequired[str | None]


# ── (b) Protocol nos callables já injetados (F4b) ──────────────────────────


class Embedder(Protocol):
    def __call__(self, text: str) -> list[float]: ...


class Downloader(Protocol):
    # Parametros pos-only: nomes divergentes entre implementacoes
    # (ex.: `default_read_screen(video_path)`) continuam conformes.
    def __call__(self, message: dict[str, Any], /) -> dict[str, Any]: ...


class Transcriber(Protocol):
    def __call__(self, filepath: str, /) -> dict[str, Any]: ...


class Describer(Protocol):
    def __call__(self, filepath: str, /) -> dict[str, Any]: ...


class ScreenReader(Protocol):
    def __call__(self, filepath: str, /) -> dict[str, Any]: ...


class Ingester(Protocol):
    """Espelho das keywords que `process_message` passa adiante."""

    def __call__(  # noqa: PLR0913 -- espelho da API kwargs de ingest_text
        self,
        text: str,
        /,
        *,
        source_url: str | None = None,
        title: str | None = None,
        platform: str = "manual",
        doc_type: str = "text",
        language: str = "pt",
        ig_pk: str | None = None,
        extra_tags: list[str] | None = None,
        categories: list[str] | None = None,
        raw_file_path: str | None = None,
    ) -> dict[str, Any]: ...

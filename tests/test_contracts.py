"""Contratos tipados do repositório (audit F4: TypedDict/Protocol/draft).

Três frentes do backlog #5, aqui como contrato:
(a) TypedDicts para os retornos mais indexados por string
    (`built["text"]`, `res["status"]` — F4a);
(b) `Protocol` nos callables já injetados (F4b);
(c) `save_document(session, draft, embed_fn, embedding_model)` derruba os
    16 params por coluna para ~4 (F4c) — a assinatura é medida aqui.
"""

from __future__ import annotations

import inspect
from typing import get_args, get_origin, get_type_hints


def test_contracts_module_exists():
    from core import contracts  # noqa: PLC0415 -- RED é o ImportError

    assert "ok" in contracts.OkResult.__annotations__
    assert "status" in contracts.ProcessResult.__annotations__
    assert "text" in contracts.DocBuilt.__annotations__


def test_save_document_takes_a_draft_instead_of_16_column_params():
    from infra.db import repository  # noqa: PLC0415

    params = inspect.signature(repository.save_document).parameters
    assert "draft" in params, "save_document deve receber um DocumentDraft"
    # PLR0913: funções com >5 args exigem justificativa; o draft é a justificativa.
    max_params = 5  # PLR0913: funcao com >5 args exige justificativa
    assert len(params) <= max_params, f"ainda são {len(params)} parâmetros"


def test_injected_callables_are_protocol_typed():
    from core.contracts import Downloader, Ingester, Transcriber  # noqa: PLC0415
    from workers import ig_worker  # noqa: PLC0415

    hints = get_type_hints(ig_worker.process_message)
    assert (
        get_origin(hints["download"]) is Downloader or hints["download"] is Downloader
    )
    assert hints["transcribe"] is not None
    # Optional[Transcriber] -> Transcriber sob args
    assert Transcriber in get_args(hints["transcribe"])
    assert hints["ingest"] is Ingester


def test_document_builders_declare_the_docbuilt_shape():
    from core.contracts import DocBuilt, DocBuiltErr, ProcessResult  # noqa: PLC0415
    from workers import ig_worker  # noqa: PLC0415

    assert "text" in DocBuilt.__annotations__
    for fn in (ig_worker._build_video_document, ig_worker._build_image_document):  # pyright: ignore[reportPrivateUsage]
        args = get_args(get_type_hints(fn)["return"])
        assert DocBuilt in args and DocBuiltErr in args
    assert get_type_hints(ig_worker.process_message)["return"] is ProcessResult

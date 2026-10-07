"""O fatiamento de `knowledge.py` em ingest/query/export é contratado aqui.

O audit (F2/SRP) exige separar os 5 domínios do facard em ~4 módulos
(ingestão / consulta / catálogo-export + fachada), mantendo a superfície
pública atual — `from core.knowledge import knowledge` e os patches de
teste em `knowledge.llm`/`knowledge.db`/`knowledge.vault` precisam seguir
funcionando. Os testes de comportamento continuam em test_core_knowledge.py;
este arquivo garante ONDE o código vive (`__module__` — re-exportar puro
não passa) e que a fachada expõe os âncoras compartilhados.
"""

from __future__ import annotations


def test_ingest_module_owns_the_ingestion_pipelines():
    from core.knowledge import ingest  # noqa: PLC0415 -- RED é o ImportError

    assert ingest.ingest_text.__module__ == "core.knowledge.ingest"
    assert ingest.ingest_video.__module__ == "core.knowledge.ingest"
    assert ingest.ingest_audio.__module__ == "core.knowledge.ingest"
    assert ingest.ingest_markdown.__module__ == "core.knowledge.ingest"


def test_query_module_owns_the_rag_consultas():
    from core.knowledge import query  # noqa: PLC0415

    assert query.search.__module__ == "core.knowledge.query"
    assert query.ask.__module__ == "core.knowledge.query"
    assert query.reindex.__module__ == "core.knowledge.query"


def test_export_module_owns_the_catalog_and_exports():
    from core.knowledge import export  # noqa: PLC0415

    assert export.export_search.__module__ == "core.knowledge.export"
    assert export.list_documents.__module__ == "core.knowledge.export"
    assert export.export_documents.__module__ == "core.knowledge.export"


def test_facade_reexports_the_whole_public_surface():
    from core.knowledge import export, ingest, knowledge, query  # noqa: PLC0415

    assert knowledge.ingest_text is ingest.ingest_text
    assert knowledge.ingest_video is ingest.ingest_video
    assert knowledge.ingest_markdown is ingest.ingest_markdown
    assert knowledge.search is query.search
    assert knowledge.ask is query.ask
    assert knowledge.reindex is query.reindex
    assert knowledge.export_search is export.export_search
    assert knowledge.list_documents is export.list_documents
    assert knowledge.export_documents is export.export_documents


def test_facade_keeps_the_shared_infra_anchors():
    # test_core_knowledge.py faz monkeypatch.setattr(knowledge.llm/db/vault, ...)
    # -- a fachada precisa seguir expondo os MESMOS objetos de módulo.
    from core.knowledge import knowledge  # noqa: PLC0415
    from infra import db, vault  # noqa: PLC0415
    from infra.llm import client as llm  # noqa: PLC0415

    assert knowledge.llm is llm
    assert knowledge.db is db
    assert knowledge.vault is vault

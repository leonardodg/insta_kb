"""Consulta RAG: busca semântica, resposta com citação e reindexação.

Extraído de `core.knowledge.knowledge` (audit F2/SRP, domínio 4 do
facade). Dono de `search` (embedding + busca no Postgres), `ask` (monta o
prompt com o contexto encontrado e cita as fontes) e `reindex`
(refaz os embeddings de todos os documentos).

`ask` chama `search` do MESMO módulo de propósito: os testes de
comportamento trocam `query.search` por um fake e verificam que a resposta
usa aquele resultado — a resolução é local, sem rebote pela fachada.
"""

from __future__ import annotations

from core.contracts import AskOk, ErrResult, ReindexOk, SearchOk
from infra import db
from infra.llm import client as llm


def search(query: str, top_k: int = 5) -> SearchOk | ErrResult:
    if not query or not query.strip():
        return {"ok": False, "error": "empty query"}
    session = db.get_session()
    try:
        results = db.search_documents(session, query, embed_fn=llm.embed, top_k=top_k)
    except Exception as e:
        return {"ok": False, "error": f"search failed: {e}"}
    finally:
        session.close()
    return {"ok": True, "query": query, "results": results}


def ask(query: str, top_k: int = 3) -> AskOk | ErrResult:
    search_result = search(query, top_k=top_k)
    if search_result["ok"] is False:
        return search_result

    results = search_result["results"]
    if not results:
        return {
            "ok": True,
            "query": query,
            "answer": "Nada encontrado na base de conhecimento para essa pergunta.",
            "sources": [],
        }

    context = "\n\n---\n\n".join(
        f"[{r.get('title') or 'sem título'}] {' '.join(r['snippets'])}" for r in results
    )
    prompt = (
        "Responda à pergunta do usuário usando APENAS o contexto abaixo, extraído "
        "da base de conhecimento pessoal dele. Se o contexto não tiver a resposta, "
        "diga isso "
        "claramente em vez de inventar.\n\n"
        f"Contexto:\n{context}\n\nPergunta: {query}\n\nResposta:"
    )
    try:
        answer = llm.chat(prompt)
    except Exception as e:
        return {"ok": False, "error": f"LLM chat failed: {e}"}

    sources = [
        {
            "document_id": r["document_id"],
            "title": r.get("title"),
            "source_url": r.get("source_url"),
        }
        for r in results
    ]
    return {"ok": True, "query": query, "answer": answer, "sources": sources}


def reindex(embedding_model: str | None = None) -> ReindexOk | ErrResult:
    session = db.get_session()
    try:
        count = db.reindex_all(
            session,
            embed_fn=llm.embed,
            embedding_model=embedding_model or llm.EMBEDDING_MODEL,
        )
    except Exception as e:
        session.rollback()
        return {"ok": False, "error": f"reindex failed: {e}"}
    finally:
        session.close()
    return {"ok": True, "documents_reindexed": count}

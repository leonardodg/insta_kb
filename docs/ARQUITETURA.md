# Arquitetura — insta_kb

Três leituras do mesmo sistema: o que existe (estrutura), com que é feito
(stack) e como os dados correm (fluxo). Visão geral na
[home](index.md); detalhes operacionais no `HANDOFF.md` do repositório.

## Estrutura

```mermaid
graph TD
  subgraph interfaces[Interfaces — mesma função por baixo]
    mcp["mcp_server/server.py — 15 tools · streamable-http :8849"]
    api["api/main.py — 10 endpoints FastAPI · :8084"]
  end

  subgraph services[src/services]
    igctl["ig_control.py — start/stop do worker"]
  end

  subgraph core[src/core]
    facade["knowledge/knowledge.py — fachada"]
    ingest["knowledge/ingest.py — resumo+tutorial via LLM"]
    query["knowledge/query.py — busca híbrida + RAG"]
    export["knowledge/export.py — export .md"]
    contracts["contracts.py — pares *Ok | ErrResult"]
    config["settings/config.py — .env tipado"]
  end

  subgraph workers[src/workers]
    consumer["consumer.py — loop RabbitMQ"]
    igworker["ig_worker.py — orquestração"]
    media["media_download.py — baixa mídia"]
    text["text.py — documento de texto"]
    screen["screen.py — estado/progresso"]
  end

  subgraph infra[src/infra]
    db[("db — Postgres 16 + pgvector")]
    queue["queue — RabbitMQ (ig.saved + DLQ)"]
    ig["instagram — instagrapi"]
    llm["llm — Ollama (chat/embed/vision)"]
    trans["transcriber — faster-whisper"]
    dl["downloader — yt-dlp"]
    gpu["gpu_lock — 1 job por vez"]
    vault["vault — export Obsidian"]
  end

  mcp --> facade
  api --> facade
  mcp --> igctl
  igctl --> queue
  queue --> consumer
  consumer --> igworker
  igworker --> media
  igworker --> text
  igworker --> screen
  igworker --> facade
  media --> trans
  media --> dl
  media --> ig
  text --> llm
  facade --> query
  facade --> ingest
  facade --> export
  query --> db
  ingest --> db
  ingest --> llm
  export --> vault
```

## Stack

```mermaid
graph LR
  subgraph docker["Docker — rede insta-kb-net"]
    pg["Postgres 16 + pgvector :5433"]
    rmq["RabbitMQ :5673 (mgmt :15673)"]
    oll["Ollama — lfm2:24b · mxbai-embed-large · qwen2.5vl:7b"]
    api_c["api · uvicorn :8084"]
    mcp_c["mcp · streamable-http :8849"]
    wrk["worker · ig-worker"]
  end

  subgraph host[Host]
    uv["Python ≥ 3.14 + uv"]
    fm["fastmcp 4.0.11"]
    fa["FastAPI"]
    av["av==18.1.0 + faster-whisper 1.2.1"]
    ig["instagrapi + yt-dlp (fallback)"]
    pq["pre-commit: ruff · pyright strict · bandit · pip-audit"]
  end

  uv --> fm
  uv --> fa
  uv --> av
  uv --> ig
  uv --> pq
  fm --> mcp_c
  fa --> api_c
  mcp_c --> pg
  mcp_c --> rmq
  mcp_c --> oll
  api_c --> pg
  wrk --> rmq
  wrk --> oll
  wrk --> av
  wrk --> ig
```

## Fluxo

```mermaid
sequenceDiagram
  participant IG as Instagram
  participant S as ig_sync_saved
  participant Q as RabbitMQ ig.saved
  participant W as ig-worker (GPU)
  participant K as core.knowledge
  participant DB as Postgres + pgvector
  participant U as MCP (15) / REST (10)

  IG->>S: posts salvos (instagrapi, dedup ig_pk)
  S->>Q: publica 1 msg por post (durable)
  Note over Q: retry ×3 → ig.saved.dead (DLQ)
  Q->>W: consome um por vez
  W->>W: vídeo → faster-whisper / imagem → vision LLM
  W->>K: ingest_text (resumo + tutorial + tags)
  K->>DB: chunks + embeddings (mxbai-embed-large)
  U->>DB: knowledge_search (RRF) / knowledge_ask (RAG)
  DB-->>U: resultados / resposta com fontes
```

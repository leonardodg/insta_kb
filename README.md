<div align="center">

# Insta Knowledge Base

### Everything you save on Instagram, turned into a searchable, local knowledge base. No cloud, no manual steps.

[![License: MIT](https://img.shields.io/badge/License-MIT-1de9d6.svg)](LICENSE)
[![Python 3.14+](https://img.shields.io/badge/python-3.14+-3776ab.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-FastMCP%204.x-8a2be2.svg)](https://gofastmcp.com)
[![pgvector](https://img.shields.io/badge/Postgres-pgvector-336791.svg)](https://github.com/pgvector/pgvector)
[![Tests](https://img.shields.io/badge/tests-206%20passing-1de9d6.svg)](tests/)
[![Types](https://img.shields.io/badge/pyright-strict-2f73bf.svg)](pyrightconfig.json)

**[Docs](https://leonardodg.github.io/insta_kb/)** ·
**[REST API docs (Swagger)](http://127.0.0.1:8084/docs)** ·
**[ReDoc](http://127.0.0.1:8084/redoc)** ·
**[MCP tools](#-mcp-tools)** ·
**[Installation](#-quick-start)**

</div>

Save a post on Instagram. Minutes later it is downloaded, transcribed (video)
or described (image), summarized by a local LLM, chunked, embedded, and
sitting in Postgres — searchable by keyword, by meaning, or by asking a
question in plain language and getting an answer with sources. Nothing
leaves the machine: no OpenAI key, no SaaS, no post ever uploaded anywhere
except back into your own database.

This is the real implementation of a domain that used to live, by accident,
inside a video-generation project ([`minimax-video-factory`](https://github.com/leonardodg/minimax-video-factory)).
It was extracted whole — code, and the real production data (3403 documents,
12755 chunks, 12755 embeddings) — into its own repo, its own stack, its own
test suite.

---

## 📋 Features

- 📥 **Auto-ingest saved Instagram posts** — a durable queue + a dedicated
  worker, not a script you have to remember to run
- 🧠 **Local LLM enrichment** — summary, tutorial, objectives and tags via
  Ollama, zero API cost, zero data leaving the machine
- 🔍 **Hybrid search** — keyword + semantic (pgvector), fused with
  reciprocal rank fusion
- 💬 **RAG that cites its sources, and says "I don't know"** — answers come
  only from your own base; it is not allowed to fill gaps with general
  knowledge
- 🎬 **Video transcribed, images described** — faster-whisper for video/audio,
  a local vision model for photos and carousels
- 📤 **Export to readable `.md`** — pick documents by id, by search, or "the
  latest N", land them under `<output_dir>/Knowledge/`
- 🔌 **Two interfaces, one codebase** — 15 MCP tools for an AI agent, a REST
  API (FastAPI, Swagger/ReDoc built in) for everything else — same functions
  underneath, zero duplicated logic
- ⚰️ **Durable, not best-effort** — RabbitMQ with a dead-letter queue and a
  retry policy; a crash doesn't lose a post, it parks it for inspection
- 🔒 **Fully local** — Postgres, RabbitMQ, the LLM and the worker all run on
  your machine; Instagram and (optionally) YouTube are the only outbound
  calls
- ✅ **206 tests, pyright strict, bandit, pip-audit** — all clean, enforced by
  `pre-commit`

---

## 🆚 Why not just a scraper script

A few projects already enumerate saved Instagram posts
([`paperfoot/clinstagram`](https://github.com/paperfoot/clinstagram),
[`SamurAIGPT/ai-knowledge-base`](https://github.com/SamurAIGPT/ai-knowledge-base)).
The difference here is infrastructure, not just surface area:

| | This project | A one-shot scraping script |
|---|---|---|
| **Durability** | RabbitMQ queue, survives a crash mid-run | one process, one chance |
| **Retries** | 3 attempts, then a DLQ you can inspect and replay | silently skipped or crashes the whole run |
| **Resumability** | dedup on `ig_pk`; re-running costs nothing | re-download everything, or track state yourself |
| **Storage** | Postgres + pgvector, hybrid search, RAG | files on disk, `grep` is the search engine |
| **Interfaces** | MCP tools **and** a documented REST API | whatever the script happens to print |
| **Rate-limit awareness** | configurable pacing between posts (`IG_WORKER_MIN_INTERVAL`) | however fast the loop runs, until Instagram notices |

---

## Technologies and Tools

### Core
| Technology | Role |
|---|---|
| **Python** ≥ 3.14 + **uv** | Application and dependency management |
| **Postgres 16 + pgvector** | Source of truth: documents, chunks, embeddings |
| **SQLAlchemy 2** | ORM access to Postgres |
| **RabbitMQ** | Durable queue (`ig.saved`) + dead-letter queue + a worker-control channel |
| **Ollama** (`lfm2:24b`, `mxbai-embed-large`, `qwen2.5vl:7b`) | Local summarization, embeddings, and image description |

### Ingestion
| Technology | Role |
|---|---|
| **instagrapi** | Authenticated enumeration and download of saved posts (session-based, no password) |
| **yt-dlp** | Fallback download path for public posts |
| **faster-whisper** | Local, GPU-accelerated transcription with timestamps |

### Interfaces
| Technology | Role |
|---|---|
| **FastMCP 4.x** | MCP server — 15 tools, streamable-http (`:8849`) |
| **FastAPI** | REST API — 10 endpoints, automatic Swagger UI + ReDoc |
| **pydantic-settings** | Typed, validated configuration from a single `.env` |

### Quality
| Technology | Role |
|---|---|
| **pytest** | 206 tests, every external service mocked at the boundary |
| **ruff** | Lint + format, strict rule set (E, F, I, W, PL) |
| **pyright** | Strict type checking — `src/` has zero errors |
| **bandit** + **pip-audit** | Security linting and dependency CVE scanning |
| **pre-commit** | Enforces all of the above before a commit lands |

---

## 🏗 Architecture

```
Instagram saved posts
   │  ig_sync_saved (instagrapi, dedup on ig_pk)
   ▼
ig.saved  (RabbitMQ, durable)  ──retry×3──►  ig.saved.dead  (DLQ)
   │
   ▼
ig-worker  (daemon, one message at a time)
   │
   ├─ video     → faster-whisper (GPU)  → LLM summary + tutorial
   └─ image(s)  → local vision LLM, one call per photo → LLM summary + tutorial
      (a carousel: first file decides video vs. image handling)
   │
   ▼
knowledge.ingest_text → chunk → embed (mxbai-embed-large) → Postgres + pgvector
   │
   ├──► MCP tools      (src/mcp_server/server.py, 15 tools, stdio)
   └──► REST API       (src/api/main.py, FastAPI, Swagger at /docs)
```

Both interfaces call the exact same functions in `core.knowledge` /
`mcp_server.server` — there is no business logic in the API layer, and none
duplicated between the two surfaces. Every cross-module boundary is typed in
`core/contracts.py` as `*Ok | ErrResult` pairs, so both surfaces return the
same shapes and pyright enforces it. See the module docstring in
`src/api/main.py` for exactly which tools are MCP-only and why
(`ig_sync_saved`, `knowledge_ingest_*`, `knowledge_reindex` — long-running or
too easy to trigger by accident over plain HTTP).

Diagrams (structure, stack, flow — Mermaid): **[docs/ARQUITETURA.md](docs/ARQUITETURA.md)**,
rendered on the **[docs site](https://leonardodg.github.io/insta_kb/ARQUITETURA/)**.

---

## 🚀 Quick Start

### Prerequisites

| Resource | Notes |
|---|---|
| Docker + Compose | Postgres (pgvector image) + RabbitMQ |
| [uv](https://docs.astral.sh/uv/) | Dependency management, Python ≥ 3.14 |
| [Ollama](https://ollama.com/) | Running on the host, with `lfm2:24b`, `mxbai-embed-large`, `qwen2.5vl:7b` pulled |
| Instagram session | `IG_SESSIONID` cookie value from a logged-in browser (DevTools → Application → Cookies) |

### Installation

```bash
git clone https://github.com/leonardodg/insta_kb.git
cd insta_kb

# 0. Configure
cp .env-example .env          # edit PROJECT_ROOT, IG_SESSIONID

# 1. Install dependencies
uv sync --all-groups

# 2. Start Postgres + RabbitMQ
docker compose --env-file .env -f .devcontainer/docker-compose.yml up -d postgres rabbitmq

# 3. Run the test suite
uv run pytest -q                      # 206 passed, no services needed — everything's mocked

# 4. Start the REST API
uv run uvicorn api.main:app --reload --app-dir src --port 8084
# → http://127.0.0.1:8084/docs  (Swagger UI, interactive)

# 5. Enqueue your saved posts and start the worker
uv run python -c "from mcp_server import server; print(server.ig_sync_saved())"
uv run python -m workers.ig_worker
```

The worker and the API/MCP server are **separate processes** on purpose —
the worker holds a GPU model (Whisper) and paces itself against Instagram's
rate limits; the API should stay responsive regardless of what the worker is
doing.

---

## 🔌 MCP tools

Registered in `.mcp.json` as `insta-kb` over streamable-http
(`http://127.0.0.1:8849/mcp`, served by the `mcp` container). Local stdio
still works: `MCP_TRANSPORT=stdio uv run python src/mcp_server/server.py`.

### Instagram sync
| Tool | What it does |
|---|---|
| `ig_sync_saved` | Enqueue saved posts (dedup on `ig_pk` unless `reprocessar=True`) |
| `ig_queue_status` | Queue depth (`ig.saved`), dead-letter count, active consumers |
| `ig_worker_start` | Resume the worker (publishes a `start` control message) |
| `ig_worker_stop` | Pause the worker — use before anything GPU-heavy on a shared card |
| `ig_get_progress` | Last N posts processed, from the worker's state file |

### Knowledge base
| Tool | What it does |
|---|---|
| `knowledge_ingest_text` | Summarize and store a text you already have |
| `knowledge_ingest_markdown` | Import `.md` files (Obsidian-compatible); reuses an existing `## Summary` and skips the LLM |
| `knowledge_ingest_video` | Download, transcribe and document a video |
| `knowledge_ingest_audio` | Transcribe and document a podcast/audio file |
| `knowledge_search` | Hybrid search — keyword and semantic, fused with RRF |
| `knowledge_ask` | RAG: answer a question from the base only, with sources |
| `knowledge_reindex` | Recompute chunks and embeddings for every document |
| `kb_list_documents` | Paginated catalog browse — filter by `platform`/`doc_type`/`tag` |
| `kb_export_search` | Preview which documents an export would include (read-only) |
| `kb_export` | Write selected documents as `.md` files |

---

## 🌐 REST API

10 endpoints, `src/api/main.py`, every one a thin wrapper around the same
function its MCP-tool counterpart calls. Full interactive docs at `/docs`
(Swagger) and `/redoc` once the server is running.

| Endpoint | Mirrors |
|---|---|
| `GET /healthcheck` | — (process liveness only) |
| `GET /ig/queue-status` | `ig_queue_status` |
| `GET /ig/progress` | `ig_get_progress` |
| `POST /ig/worker/start` | `ig_worker_start` |
| `POST /ig/worker/stop` | `ig_worker_stop` |
| `GET /knowledge/search` | `knowledge_search` |
| `POST /knowledge/ask` | `knowledge_ask` |
| `GET /knowledge/documents` | `kb_list_documents` — paginated, filterable |
| `GET /knowledge/export/search` | `kb_export_search` |
| `POST /knowledge/export` | `kb_export` |

**Deliberately MCP-only** (not exposed over HTTP): `ig_sync_saved` (long,
rate-limit-sensitive scan), `knowledge_ingest_*` (can take minutes —
synchronous HTTP is the wrong shape until there's a background-job design),
`knowledge_reindex` (rebuilds every embedding — too easy to trigger by
accident over a plain `POST`).

```bash
# Browse the catalog, newest first, only Instagram videos
curl "http://127.0.0.1:8084/knowledge/documents?platform=instagram&doc_type=video&limit=10"

# Ask a question, answered only from what you've saved
curl -X POST "http://127.0.0.1:8084/knowledge/ask" --data-urlencode "query=o que eu salvei sobre docker volumes?"
```

---

## ⚙️ Configuration (`.env`)

```bash
PROJECT_ROOT=/path/to/insta_kb

# Postgres (pgvector) — note the non-default port, this is NOT 5432
POSTGRES_PORT=5433
DATABASE_URL=postgresql+psycopg://kb:kb@127.0.0.1:5433/knowledge

# RabbitMQ — note the non-default ports, this is NOT 5672/15672
RABBITMQ_PORT=5673
RABBITMQ_MANAGEMENT_PORT=15673
RABBITMQ_URL=amqp://guest:guest@localhost:5673/
RABBITMQ_QUEUE=ig.saved

# Instagram — session cookie, never a password
IG_SESSIONID=
IG_WORKER_MIN_INTERVAL=0    # seconds between posts; default is 0 (as fast as
                             # possible). Two full scans in an hour got 429s and
                             # dropped collections from the listing in practice
                             # — a production run used 90 for ~40 posts/hour.

# Local LLM (Ollama, must be running on the host)
LLM_MODEL=lfm2:24b
EMBEDDING_MODEL=mxbai-embed-large
OLLAMA_VISION_MODEL=qwen2.5vl:7b
```

The non-default ports (`5433`, `5673`, `15673`) exist specifically so this
stack can run **next to** `minimax-video-factory`'s own Postgres/RabbitMQ
without colliding — the two were split out of the same repo and used to
share a host. See `.env-example` for every variable, each with the reasoning
inline.

---

## 🛠 Project structure

```
insta_kb/
├── .mcp.json                      # MCP server registration (Claude Code)
├── .devcontainer/docker-compose.yml   # Postgres (pgvector) + RabbitMQ
├── src/
│   ├── core/
│   │   ├── contracts.py           # typed contracts: *Ok | ErrResult pairs per boundary
│   │   ├── settings/config.py     # pydantic-settings, single source of .env
│   │   └── knowledge/             # use cases: ingest / query / export + knowledge.py facade
│   ├── services/
│   │   └── ig_control.py          # worker start/stop control commands
│   ├── infra/
│   │   ├── db/                    # SQLAlchemy models + repository
│   │   ├── llm/                   # Ollama client (chat, embed, vision) + model registry
│   │   ├── queue/                 # RabbitMQ publisher/consumer
│   │   ├── instagram/             # instagrapi client, saved-posts enumeration
│   │   ├── downloader/            # yt-dlp fallback
│   │   ├── transcriber/           # faster-whisper
│   │   ├── gpu_lock/              # one-GPU-at-a-time lock across processes
│   │   ├── log/                   # logging config
│   │   └── vault/                 # Obsidian .md export
│   ├── workers/                   # the daemon: consumer → ig_worker → media/text/screen handlers
│   ├── mcp_server/server.py       # 15 @mcp.tool() definitions
│   └── api/main.py                # FastAPI app, 10 endpoints
├── scripts/
│   ├── ig_pendentes.py            # which documents still need reprocessing
│   ├── ig_reprocessar.py          # re-run the pipeline on existing media, --aplicar to write
│   ├── ig_relatorio.sh            # read-only operational report (queue, db, disk, GPU)
│   └── ig_rodar_tudo.sh           # chain reprocess → resume sync (they share the GPU)
├── tests/                         # 206 tests, one file per src/ module
├── typings/instagrapi/            # type stubs (instagrapi ships untyped)
└── docs/HANDOFF.md                # project state: what's done, what's pending, bugs found
```

---

## 📦 Useful commands

```bash
# Tests & quality — same checks pre-commit runs before every commit
uv run pytest -q
uv run ruff check src/ scripts/ tests/
uv run pyright src/ tests/ scripts/
uv run bandit -c pyproject.toml -r .
uv run pip-audit

# Operations
bash scripts/ig_relatorio.sh                  # queue + db + disk + GPU, read-only
uv run python scripts/ig_pendentes.py         # what still needs reprocessing
```

---

## 🔌 Claude Code MCP config

Already checked in as `.mcp.json`, picked up on trust:

```json
{
  "mcpServers": {
    "insta-kb": {
      "type": "http",
      "url": "http://127.0.0.1:8849/mcp"
    }
  }
}
```

---

## ⚠️ Honest limitations

- **The worker is a single process.** Two consumers on the same queue caused
  an OOM on a shared GPU in practice (one of them running stale code after a
  restart that didn't fully kill the old process) — `scripts/ig_rodar_tudo.sh`
  checks the process count for exactly this reason. Run exactly one.
- **A video carousel only transcribes its first video.** If the first
  downloaded file is a video, that's the one that gets transcribed — other
  slides are not. An all-image carousel is different: every image gets
  described and the results are merged into one document.
- **Session cookies expire.** `IG_SESSIONID` is not a permanent credential;
  when Instagram invalidates it, re-copy it from a logged-in browser.
- **The vision model sees one frame at a time.** Image description has no
  memory of a previous post — if you want consistent categorization, the
  category vocabulary comes from your own Instagram collections, not a
  fixed list.
- **Rate limits are real and silent.** Too fast a scan does not error
  cleanly — it drops collections from the listing. `IG_WORKER_MIN_INTERVAL`
  exists because of exactly that, measured in production.
- **Reindexing is O(every document).** `knowledge_reindex` walks the whole
  base; there is no incremental mode yet.

---

## 🤝 Contributing

<img src="https://avatars.githubusercontent.com/u/1678290?s=400&u=2f875356b82f055057b6e9679c0b66001b9b29f9&v=4" width="120" title="LeoDG">

Issues and pull requests are welcome. Every change is expected to pass the
full `pre-commit` suite (`ruff`, `pyright` strict, `bandit`, `pip-audit`) —
install it once with `uv run pre-commit install`.

## 📄 License

MIT — see [LICENSE](LICENSE).

## 📮 Contact

LeoDG — [@le0dg](https://www.linkedin.com/in/le0dg)

- **Repository:** https://github.com/leonardodg/insta_kb
- **Related project:** [minimax-video-factory](https://github.com/leonardodg/minimax-video-factory) — the video-generation sibling this repo was split from

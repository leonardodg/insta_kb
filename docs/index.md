# Insta Knowledge Base

Everything you save on Instagram, turned into a searchable, local knowledge
base. No cloud, no manual steps: a durable queue picks the post up, a worker
transcribes/describes it, a local LLM documents it, and it lands in Postgres
+ pgvector — searchable by keyword, by meaning, or by asking a question in
plain language.

## Where to start

- **[Arquitetura](ARQUITETURA.md)** — structure, stack and data-flow diagrams
  (Mermaid).
- **[Repositório](https://github.com/leonardodg/insta_kb)** — README with the
  quick start, the 15 MCP tools and the 10 REST endpoints.

## Interfaces

| Surface | Port | What it is |
|---|---|---|
| MCP (streamable-http) | `:8849` | 15 tools for an AI agent — `.mcp.json` |
| REST (FastAPI) | `:8084` | 10 endpoints, Swagger at `/docs`, ReDoc at `/redoc` |

Both are thin wrappers over the same `core.knowledge` functions.

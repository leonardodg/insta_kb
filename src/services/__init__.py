"""Application services shared by every adapter (REST API, MCP server).

SOLID audit #2 (F5): adapters are siblings -- they import this layer (and
`core`/`infra` below it), never each other. Anything both surfaces need
lives here instead of being re-exported through one of them.
"""

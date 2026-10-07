from pathlib import Path

from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

# Absolute, not "../../.env": a relative env_file is resolved against the
# process's CWD at startup, not against this file's location. Running from
# the repo root (the common case for `uv run python src/...`) made
# "../../.env" resolve two levels ABOVE the repo -- .env silently failed to
# load and every setting fell back to its field default (e.g. Postgres on
# 5432, the video-factory's port, instead of this project's 5433). Caught
# 2026-10-06 running `knowledge.list_documents()` against the real database.
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    PROJECT_ROOT: str
    APP_NAME: str = "Insta Knowledge Base"
    LOG_LEVEL: str = "INFO"
    APP_ENV: str = "development"

    MCP_TRANSPORT: str = "stdio"
    MCP_HOST: str = "0.0.0.0"
    MCP_PORT: int = 8099

    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8085

    POSTGRES_SCHEMA: str = "postgresql+psycopg"
    POSTGRES_USER: str = "kb"
    POSTGRES_PASSWORD: str = "kb"
    POSTGRES_DB: str = "knowledge"
    POSTGRES_PORT: int = 5432
    POSTGRES_CONTAINER: str = "insta-kb-postgres"
    POSTGRES_HOST: str = "127.0.0.1"

    DATABASE_URL: MultiHostUrl = MultiHostUrl.build(
        scheme=POSTGRES_SCHEMA,
        username=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        path=POSTGRES_DB,
    )

    RABBITMQ_SCHEMA: str = "amqp"
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_CONTAINER: str = "insta-kb-rabbitmq"
    RABBITMQ_HOST: str = "rabbitmq"

    RABBITMQ_URL: str = "{RABBITMQ_SCHEMA}://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}@{RABBITMQ_HOST}:{RABBITMQ_PORT}/"
    RABBITMQ_QUEUE: str = "ig.saved"
    # Retry policy for ig.saved processing failures (ig_queue.handle_failure).
    IG_MAX_ATTEMPTS: int = 3
    # pika BlockingConnection: how long basic_publish may block on a broker
    # under memory pressure (connection.blocked) before raising.
    RABBITMQ_BLOCKED_TIMEOUT: int = 300

    # -- Ollama / LLM client (infra/llm) --
    OLLAMA_URL: str = "http://localhost:11434"
    LLM_PROVIDER: str = "ollama"
    LLM_MODEL: str = "lfm2:24b"
    EMBEDDING_MODEL: str = "mxbai-embed-large"
    EMBEDDING_DIM: int = 1024
    OPENAI_API_URL: str = ""
    OPENAI_API_KEY: str = ""
    LLM_TIMEOUT: float = 900.0
    # How long Ollama keeps a model resident after a call. "0" hands memory
    # back immediately; see infra/llm for why this matters on a shared GPU.
    OLLAMA_KEEP_ALIVE: str = "0"
    EMBED_TIMEOUT: float = 120.0
    LLM_NUM_CTX: int = 8192
    VISION_NUM_CTX: int = 4096
    OLLAMA_VISION_MODEL: str = "qwen2.5vl:7b"

    # -- Obsidian export (infra/vault / core/knowledge) --
    VAULT_PATH: str = ""

    # -- Instagram enumeration (infra/instagram) --
    IG_SESSIONID: str = ""
    IG_DESCARTADOS_FILE: str = "output/ig-descartados.jsonl"

    # -- ig-worker daemon --
    IG_DOWNLOADS_DIR: str = "downloads/ig"
    IG_DELETE_AFTER_INGEST: bool = False
    IG_STATE_FILE: str = "downloads/ig/state.json"
    IG_WORKER_MIN_INTERVAL: float = 0.0
    IG_SCREEN_FRAMES: int = 4
    WHISPER_MODEL: str = "small"
    WHISPER_DEVICE: str = "cuda"

    model_config = SettingsConfigDict(
        case_sensitive=True,
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        # .env is shared with docker-compose.yml (PG_UID, POSTGRES_DATA_DIR,
        # RABBITMQ_MANAGEMENT_PORT, ...) -- those are compose-only, not
        # Settings fields. Without "ignore", loading the real .env raises
        # `extra_forbidden` for every key this class doesn't declare.
        extra="ignore",
    )


settings = Settings(PROJECT_ROOT="/app")

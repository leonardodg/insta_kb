from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(
        case_sensitive=True, env_file="../../.env", env_file_encoding="utf-8"
    )


settings = Settings(PROJECT_ROOT="/app")

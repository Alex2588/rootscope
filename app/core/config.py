"""Application settings, loaded from the environment / `.env`."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ROOTSCOPE_",
        extra="ignore",
    )

    env: str = "local"
    app_name: str = "Incident Investigation Assistant"

    # Unprefixed by convention (set directly by docker-compose / most Postgres tooling),
    # so this bypasses ROOTSCOPE_ rather than looking for ROOTSCOPE_DATABASE_URL.
    # Default matches docker-compose's published port for use outside the container
    # (e.g. running `alembic upgrade head` or `uv run uvicorn` from the host).
    database_url: str = Field(
        default="postgresql+asyncpg://rootscope:rootscope@localhost:5432/rootscope",
        validation_alias="DATABASE_URL",
    )
    # Unprefixed for the same reason as DATABASE_URL; credentials match docker-compose.
    rabbitmq_url: str = Field(
        default="amqp://rootscope:rootscope@localhost:5672/",
        validation_alias="RABBITMQ_URL",
    )

    relay_batch_size: int = 10
    relay_poll_interval_seconds: float = 1.0
    relay_confirm_timeout_seconds: float = 10.0
    relay_reconnect_max_delay_seconds: float = 30.0


@lru_cache
def get_settings() -> Settings:
    return Settings()

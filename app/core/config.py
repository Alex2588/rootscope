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


@lru_cache
def get_settings() -> Settings:
    return Settings()

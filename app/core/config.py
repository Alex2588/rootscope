"""Application settings, loaded from the environment / `.env`."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="ROOTSCOPE_",
        extra="ignore",
    )

    env: str = "local"
    app_name: str = "Incident Investigation Assistant"


@lru_cache
def get_settings() -> Settings:
    return Settings()

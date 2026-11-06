# Must run before anything imports app.core.config: real env vars outrank .env in
# pydantic-settings, and get_settings() is lru_cache'd, so whichever value is resolved on
# first call wins for the whole process. This file has no other imports so that unit
# tests (e.g. tests/test_health.py) never require Docker/PostgreSQL to be available.
import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://rootscope:rootscope@localhost:5432/rootscope_test"
)
os.environ.setdefault("RABBITMQ_URL", "amqp://rootscope:rootscope@localhost:5672/rootscope_test")

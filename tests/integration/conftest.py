"""Fixtures here require Docker/PostgreSQL — scoped to tests/integration/ only so
tests/test_health.py and any future unit tests never pay that cost.

Sequential execution assumed (no pytest-xdist): "clean before each test" truncates
shared tables, which would race across parallel workers sharing one test database.
"""

import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.core.config import get_settings
from app.db.base import Base
from app.db.models import incident as _incident  # noqa: F401 — registers metadata
from app.db.models import investigation as _investigation  # noqa: F401 — registers metadata
from app.db.session import get_engine

_ALEMBIC_INI = Path(__file__).resolve().parent.parent.parent / "alembic.ini"


def _assert_is_test_database(url: str) -> None:
    db_name = url.rsplit("/", 1)[-1].split("?", 1)[0]
    if not db_name.endswith("_test"):
        raise RuntimeError(
            f"Refusing destructive fixture operations against database {db_name!r} — "
            "expected a name ending in '_test'. A pre-set DATABASE_URL environment "
            "variable may be overriding tests/conftest.py's default."
        )


def _admin_dsn(test_url: str) -> str:
    # CREATE DATABASE needs a connection to an existing database and must run outside a
    # transaction block — asyncpg directly, not the SQLAlchemy engine.
    parts = urlsplit(test_url.replace("+asyncpg", ""))
    return urlunsplit(parts._replace(path="/postgres"))


async def _ensure_database_exists(url: str) -> None:
    db_name = url.rsplit("/", 1)[-1]
    conn = await asyncpg.connect(_admin_dsn(url))
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", db_name)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()


async def _migrate_to_head() -> None:
    def upgrade() -> None:
        command.upgrade(Config(str(_ALEMBIC_INI)), "head")

    # alembic/env.py calls asyncio.run() internally, which raises if called from a
    # loop that's already running — to_thread gives it a fresh thread with no loop.
    await asyncio.to_thread(upgrade)


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _test_database():
    url = get_settings().database_url
    _assert_is_test_database(url)
    await _ensure_database_exists(url)
    await _migrate_to_head()
    yield


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def _clean_tables(_test_database):
    _assert_is_test_database(get_settings().database_url)
    engine = get_engine()
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(text(f'TRUNCATE TABLE "{table.name}" CASCADE'))
    yield

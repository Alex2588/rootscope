from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


@lru_cache
def get_engine() -> AsyncEngine:
    # One engine per process — it owns the connection pool.
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: callers read committed ORM attributes after the service's
    # transaction closes (e.g. building a response) without triggering a reload.
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    # No explicit rollback: AsyncSession.close() rolls back any uncommitted transaction.
    # Commit/rollback ownership belongs to the service layer — see docs/adr/0001.
    async with get_sessionmaker()() as session:
        yield session

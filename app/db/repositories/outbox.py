import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.outbox_event import OutboxEvent


async def add_outbox_event(
    session: AsyncSession, *, aggregate_id: uuid.UUID, event_type: str, payload: dict[str, Any]
) -> None:
    await session.execute(
        insert(OutboxEvent).values(
            aggregate_id=aggregate_id, event_type=event_type, payload=payload
        )
    )


async def lock_unpublished_batch(session: AsyncSession, limit: int) -> list[OutboxEvent]:
    stmt = (
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.created_at, OutboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list((await session.scalars(stmt)).all())


async def mark_published(session: AsyncSession, event_ids: Sequence[uuid.UUID]) -> None:
    await session.execute(
        update(OutboxEvent).where(OutboxEvent.id.in_(event_ids)).values(published_at=func.now())
    )

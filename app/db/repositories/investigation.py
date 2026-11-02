import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.investigation import Investigation


async def create_investigation_idempotent(
    session: AsyncSession, *, incident_id: uuid.UUID, values: dict, idempotency_key: str
) -> tuple[Investigation, bool]:
    stmt = (
        pg_insert(Investigation)
        .values(**values, incident_id=incident_id, idempotency_key=idempotency_key)
        .on_conflict_do_nothing(index_elements=[Investigation.idempotency_key])
        .returning(Investigation)
    )
    investigation = (await session.execute(stmt)).scalar_one_or_none()
    if investigation is not None:
        return investigation, True

    # Under READ COMMITTED, the next statement sees the committed conflicting row.
    existing = await session.scalar(
        select(Investigation).where(Investigation.idempotency_key == idempotency_key)
    )
    if existing is None:
        raise RuntimeError("Idempotency conflict without an existing investigation")
    return existing, False

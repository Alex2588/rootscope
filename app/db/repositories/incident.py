import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.incident import Incident


async def get_incident_by_id(session: AsyncSession, incident_id: uuid.UUID) -> Incident | None:
    return await session.get(Incident, incident_id)


async def create_incident_idempotent(
    session: AsyncSession, *, values: dict, idempotency_key: str
) -> tuple[Incident, bool]:
    stmt = (
        pg_insert(Incident)
        .values(**values, idempotency_key=idempotency_key)
        .on_conflict_do_nothing(index_elements=[Incident.idempotency_key])
        .returning(Incident)
    )
    incident = (await session.execute(stmt)).scalar_one_or_none()
    if incident is not None:
        return incident, True

    # Under READ COMMITTED, the next statement sees the committed conflicting row.
    existing = await session.scalar(
        select(Incident).where(Incident.idempotency_key == idempotency_key)
    )
    if existing is None:
        raise RuntimeError("Idempotency conflict without an existing incident")
    return existing, False

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import fingerprint
from app.db.models.incident import Incident
from app.db.repositories.incident import create_incident_idempotent, get_incident_by_id
from app.schemas.incident import IncidentCreate


class IdempotencyConflictError(Exception):
    pass


class IncidentNotFoundError(Exception):
    pass


async def create_incident(
    session: AsyncSession, payload: IncidentCreate, idempotency_key: str
) -> Incident:
    # json mode for a stable fingerprint (string-serialized datetimes); python mode for
    # the actual insert values (asyncpg wants native datetime objects).
    computed_fingerprint = fingerprint(payload.model_dump(mode="json"))
    values = {
        **payload.model_dump(mode="python"),
        "request_fingerprint": computed_fingerprint,
    }

    async with session.begin():
        incident, created = await create_incident_idempotent(
            session, values=values, idempotency_key=idempotency_key
        )
        if not created and incident.request_fingerprint != computed_fingerprint:
            raise IdempotencyConflictError(
                "Idempotency-Key was already used with a different request payload"
            )

    return incident


async def get_incident(session: AsyncSession, incident_id: uuid.UUID) -> Incident:
    async with session.begin():
        incident = await get_incident_by_id(session, incident_id)
    if incident is None:
        raise IncidentNotFoundError(f"Incident {incident_id} not found")
    return incident

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.idempotency import fingerprint
from app.db.models.investigation import Investigation
from app.db.repositories.incident import get_incident_by_id
from app.db.repositories.investigation import (
    create_investigation_idempotent,
    get_investigation_by_id,
)
from app.db.repositories.outbox import add_outbox_event
from app.messaging.contracts import InvestigationExecutionRequested
from app.schemas.investigation import InvestigationCreate
from app.services.incident import IdempotencyConflictError, IncidentNotFoundError


class InvestigationNotFoundError(Exception):
    pass


async def create_investigation(
    session: AsyncSession,
    incident_id: uuid.UUID,
    payload: InvestigationCreate,
    idempotency_key: str,
) -> Investigation:
    values = payload.model_dump(mode="json")
    # incident_id is part of "the same request": reusing a key on another incident conflicts.
    computed_fingerprint = fingerprint(str(incident_id), values)

    async with session.begin():
        if await get_incident_by_id(session, incident_id) is None:
            raise IncidentNotFoundError(f"Incident {incident_id} not found")

        investigation, created = await create_investigation_idempotent(
            session,
            incident_id=incident_id,
            values={**values, "request_fingerprint": computed_fingerprint},
            idempotency_key=idempotency_key,
        )
        if not created and investigation.request_fingerprint != computed_fingerprint:
            raise IdempotencyConflictError(
                "Idempotency-Key was already used with a different request payload"
            )

        if created:
            event = InvestigationExecutionRequested(
                investigation_id=investigation.id, expected_attempt=1
            )
            await add_outbox_event(
                session,
                aggregate_id=investigation.id,
                event_type=event.EVENT_TYPE,
                payload=event.model_dump(mode="json"),
            )

    return investigation


async def get_investigation(session: AsyncSession, investigation_id: uuid.UUID) -> Investigation:
    async with session.begin():
        investigation = await get_investigation_by_id(session, investigation_id)
    if investigation is None:
        raise InvestigationNotFoundError(f"Investigation {investigation_id} not found")
    return investigation

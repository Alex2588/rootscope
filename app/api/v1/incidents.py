import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_idempotency_key
from app.db.session import get_session
from app.schemas.incident import IncidentCreate, IncidentRead
from app.services import incident as incident_service

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IncidentRead)
async def create_incident(
    payload: IncidentCreate,
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_session),
) -> IncidentRead:
    try:
        incident = await incident_service.create_incident(
            session=session, payload=payload, idempotency_key=idempotency_key
        )
    except incident_service.IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return IncidentRead.model_validate(incident)


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(
    incident_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> IncidentRead:
    try:
        incident = await incident_service.get_incident(session=session, incident_id=incident_id)
    except incident_service.IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return IncidentRead.model_validate(incident)

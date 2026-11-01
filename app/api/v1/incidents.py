from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.schemas.incident import IncidentCreate, IncidentRead
from app.services import incident as incident_service

router = APIRouter(prefix="/incidents", tags=["incidents"])

_IDEMPOTENCY_KEY_MAX_LENGTH = 255


@router.post("", status_code=status.HTTP_201_CREATED, response_model=IncidentRead)
async def create_incident(
    payload: IncidentCreate,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_session),
) -> IncidentRead:
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Idempotency-Key header is required",
        )
    if len(idempotency_key) > _IDEMPOTENCY_KEY_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Idempotency-Key must be at most {_IDEMPOTENCY_KEY_MAX_LENGTH} characters",
        )

    try:
        incident = await incident_service.create_incident(
            session=session, payload=payload, idempotency_key=idempotency_key
        )
    except incident_service.IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return IncidentRead.model_validate(incident)

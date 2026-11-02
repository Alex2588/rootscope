import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import require_idempotency_key
from app.db.session import get_session
from app.schemas.investigation import InvestigationCreate, InvestigationRead
from app.services import incident as incident_service
from app.services import investigation as investigation_service

router = APIRouter(prefix="/incidents/{incident_id}/investigations", tags=["investigations"])


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=InvestigationRead)
async def create_investigation(
    incident_id: uuid.UUID,
    payload: InvestigationCreate,
    idempotency_key: str = Depends(require_idempotency_key),
    session: AsyncSession = Depends(get_session),
) -> InvestigationRead:
    try:
        investigation = await investigation_service.create_investigation(
            session=session,
            incident_id=incident_id,
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except incident_service.IncidentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except incident_service.IdempotencyConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return InvestigationRead.model_validate(investigation)

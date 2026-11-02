import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.domain.investigation import InvestigationStatus, InvestigationStrategy


class InvestigationCreate(BaseModel):
    # Fields such as config/model are planned for this body; rejecting them now prevents
    # clients from sending values that would be silently ignored and left out of the
    # idempotency fingerprint.
    model_config = ConfigDict(extra="forbid")

    strategy: InvestigationStrategy


class InvestigationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    incident_id: uuid.UUID
    strategy: InvestigationStrategy
    status: InvestigationStatus
    config: dict[str, Any]
    model: str | None
    report: dict[str, Any] | None
    error: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

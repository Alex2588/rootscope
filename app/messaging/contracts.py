import uuid
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field


class InvestigationExecutionRequested(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    EVENT_TYPE: ClassVar[str] = "investigation.execution_requested"

    version: Literal[1] = 1
    investigation_id: uuid.UUID
    # Authorizes exactly one claim: the attempt this event may start (ADR 0002 §10).
    expected_attempt: int = Field(ge=1)

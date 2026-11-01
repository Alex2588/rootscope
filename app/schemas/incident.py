"""Request/response models for the incidents resource."""

import uuid
from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class IncidentCreate(BaseModel):
    # Defaults to "manual" — the only source in play until F1's later extensions
    # (webhooks, RCAEval replay, etc.) land.
    source: str = Field(default="manual", max_length=50)
    external_incident_id: str | None = Field(default=None, max_length=255)
    alert_title: str = Field(min_length=1)
    alert_description: str | None = None
    service_name: str = Field(min_length=1, max_length=255)

    # Incidents originate from multiple sources (manual, Grafana, RCAEval replay) —
    # AwareDatetime rejects naive input so timezone is never silently assumed.
    window_start: AwareDatetime
    window_end: AwareDatetime

    @field_validator("window_start", "window_end")
    @classmethod
    def _normalize_utc(cls, v: datetime) -> datetime:
        # Keeps the fingerprint stable across equivalent instants with different offsets.
        return v.astimezone(UTC)

    @model_validator(mode="after")
    def _check_window_order(self) -> "IncidentCreate":
        # Mirrors ck_incidents_window_order — fail fast with a 422 rather than an
        # IntegrityError.
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        return self


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: str
    external_incident_id: str | None
    alert_title: str
    alert_description: str | None
    service_name: str
    window_start: datetime
    window_end: datetime
    created_at: datetime

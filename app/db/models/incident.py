"""The incidents table: static diagnostic context for a real-world event.

Does not track investigation execution — that's the investigations table (added in a
later migration), which references incidents via a foreign key. One incident can have
many investigations (different strategies, or repeated benchmark runs).
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Incident(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        CheckConstraint("window_end > window_start", name="ck_incidents_window_order"),
        # Non-unique: source/external-system semantics don't guarantee uniqueness across
        # all sources, and a strict unique index would silently reject legitimate data
        # from a source that reuses identifiers.
        Index("ix_incidents_source_external_id", "source", "external_incident_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    external_incident_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    alert_title: Mapped[str] = mapped_column(Text, nullable=False)
    alert_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Globally unique for now (single-client MVP, per decision — no client_id yet).
    # Scope this to (client_id, idempotency_key) once auth/multi-tenancy exists.
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # sha256 hex digest of the canonical request payload — 64 hex chars exactly.
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

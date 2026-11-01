"""Requires the real Postgres from docker-compose (`docker compose up -d db`).

Bypasses the HTTP layer deliberately: this exercises genuine concurrent connections
racing the unique-index constraint, which a synchronous test client can't produce.
"""

import asyncio
import uuid
from datetime import UTC, datetime

from app.db.repositories.incident import create_incident_idempotent
from app.db.session import get_sessionmaker


def _values() -> dict:
    return {
        "source": "manual",
        "external_incident_id": None,
        "alert_title": "Concurrent submission test",
        "alert_description": None,
        "service_name": "checkout",
        "window_start": datetime(2026, 1, 1, tzinfo=UTC),
        "window_end": datetime(2026, 1, 1, 1, tzinfo=UTC),
        "request_fingerprint": "f" * 64,
    }


async def test_concurrent_inserts_with_same_key_resolve_to_one_row() -> None:
    key = f"concurrent-{uuid.uuid4()}"
    sessionmaker = get_sessionmaker()

    async def attempt() -> tuple:
        # The repository never commits (see app/db/repositories/incident.py) — commit
        # here explicitly, mirroring what the service layer does on the real request path.
        async with sessionmaker() as session:
            result = await create_incident_idempotent(
                session, values=_values(), idempotency_key=key
            )
            await session.commit()
            return result

    results = await asyncio.gather(*(attempt() for _ in range(10)))

    ids = {incident.id for incident, _ in results}
    assert len(ids) == 1, "all concurrent attempts must resolve to the same row"

    created_flags = [created for _, created in results]
    assert created_flags.count(True) == 1, "exactly one attempt should have inserted"
    assert created_flags.count(False) == 9, "the rest should have found the existing row"

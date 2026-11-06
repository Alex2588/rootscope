import uuid
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db.models.investigation import Investigation
from app.db.models.outbox_event import OutboxEvent
from app.db.session import get_sessionmaker
from app.domain.investigation import InvestigationStrategy
from app.messaging.contracts import InvestigationExecutionRequested
from app.schemas.incident import IncidentCreate
from app.schemas.investigation import InvestigationCreate
from app.services import investigation as investigation_service
from app.services.incident import IdempotencyConflictError, create_incident


def _key() -> str:
    return f"test-{uuid.uuid4()}"


@pytest_asyncio.fixture(loop_scope="session")
async def incident_id() -> uuid.UUID:
    payload = IncidentCreate(
        alert_title="High latency on checkout",
        service_name="checkout",
        window_start=datetime(2026, 1, 1, tzinfo=UTC),
        window_end=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    async with get_sessionmaker()() as session:
        return (await create_incident(session, payload, _key())).id


async def _create(
    incident_id: uuid.UUID,
    key: str,
    strategy: InvestigationStrategy = InvestigationStrategy.LLM_ONLY,
) -> Investigation:
    async with get_sessionmaker()() as session:
        return await investigation_service.create_investigation(
            session, incident_id, InvestigationCreate(strategy=strategy), key
        )


async def _outbox_events() -> list[OutboxEvent]:
    async with get_sessionmaker()() as session:
        return list(await session.scalars(select(OutboxEvent)))


async def test_creation_inserts_one_unpublished_execution_event(incident_id: uuid.UUID) -> None:
    investigation = await _create(incident_id, _key())

    [event] = await _outbox_events()
    assert event.aggregate_id == investigation.id
    assert event.event_type == InvestigationExecutionRequested.EVENT_TYPE
    assert event.created_at is not None
    assert event.published_at is None
    assert InvestigationExecutionRequested.model_validate(event.payload) == (
        InvestigationExecutionRequested(investigation_id=investigation.id, expected_attempt=1)
    )


async def test_idempotent_replay_inserts_no_second_event(incident_id: uuid.UUID) -> None:
    key = _key()
    first = await _create(incident_id, key)
    replay = await _create(incident_id, key)

    assert replay.id == first.id
    assert len(await _outbox_events()) == 1


async def test_conflicting_replay_inserts_no_event(incident_id: uuid.UUID) -> None:
    key = _key()
    await _create(incident_id, key, InvestigationStrategy.LLM_ONLY)
    with pytest.raises(IdempotencyConflictError):
        await _create(incident_id, key, InvestigationStrategy.AGENTIC)

    assert len(await _outbox_events()) == 1


async def test_failure_after_outbox_insert_rolls_back_both_rows(
    incident_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_add = investigation_service.add_outbox_event

    async def insert_then_fail(*args, **kwargs) -> None:
        await real_add(*args, **kwargs)
        raise RuntimeError("simulated failure after the outbox insert")

    monkeypatch.setattr(investigation_service, "add_outbox_event", insert_then_fail)
    key = _key()
    with pytest.raises(RuntimeError):
        await _create(incident_id, key)

    async with get_sessionmaker()() as session:
        investigation = await session.scalar(
            select(Investigation).where(Investigation.idempotency_key == key)
        )
    assert investigation is None
    assert await _outbox_events() == []

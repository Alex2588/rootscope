"""PostgreSQL integration tests for the /api/v1/incidents endpoints."""

import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

import app.services.incident as incident_service
from app.db.models.incident import Incident
from app.db.repositories.incident import create_incident_idempotent
from app.db.session import get_sessionmaker
from app.main import app


def _payload(**overrides: object) -> dict:
    base = {
        "alert_title": "High latency on checkout",
        "service_name": "checkout",
        "window_start": "2026-01-01T00:00:00Z",
        "window_end": "2026-01-01T01:00:00Z",
    }
    base.update(overrides)
    return base


def _key() -> str:
    return f"test-{uuid.uuid4()}"


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_create_incident_success(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/incidents", json=_payload(), headers={"Idempotency-Key": _key()}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["alert_title"] == "High latency on checkout"
    assert body["service_name"] == "checkout"
    assert body["source"] == "manual"  # default
    assert body["id"]
    assert body["created_at"]


async def test_missing_idempotency_key_returns_400(client: AsyncClient) -> None:
    response = await client.post("/api/v1/incidents", json=_payload())

    assert response.status_code == 400


async def test_whitespace_idempotency_key_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/incidents", json=_payload(), headers={"Idempotency-Key": "   "}
    )

    assert response.status_code == 400


async def test_oversized_idempotency_key_returns_400(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/incidents", json=_payload(), headers={"Idempotency-Key": "k" * 256}
    )

    assert response.status_code == 400


async def test_duplicate_submission_returns_existing_incident(client: AsyncClient) -> None:
    key = _key()
    payload = _payload()

    first = await client.post("/api/v1/incidents", json=payload, headers={"Idempotency-Key": key})
    second = await client.post("/api/v1/incidents", json=payload, headers={"Idempotency-Key": key})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["created_at"] == second.json()["created_at"]


async def test_conflicting_payload_with_same_key_returns_409(client: AsyncClient) -> None:
    key = _key()
    first = await client.post(
        "/api/v1/incidents", json=_payload(), headers={"Idempotency-Key": key}
    )
    assert first.status_code == 201

    response = await client.post(
        "/api/v1/incidents",
        json=_payload(alert_title="A completely different alert"),
        headers={"Idempotency-Key": key},
    )

    assert response.status_code == 409


async def test_invalid_window_order_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/incidents",
        json=_payload(window_start="2026-01-01T01:00:00Z", window_end="2026-01-01T00:00:00Z"),
        headers={"Idempotency-Key": _key()},
    )

    assert response.status_code == 422


async def test_naive_datetime_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/incidents",
        json=_payload(window_start="2026-01-01T00:00:00", window_end="2026-01-01T01:00:00"),
        headers={"Idempotency-Key": _key()},
    )

    assert response.status_code == 422


async def test_conflict_does_not_mutate_existing_row(client: AsyncClient) -> None:
    key = _key()
    first = await client.post(
        "/api/v1/incidents", json=_payload(), headers={"Idempotency-Key": key}
    )
    assert first.status_code == 201

    conflict = await client.post(
        "/api/v1/incidents",
        json=_payload(alert_title="A completely different alert"),
        headers={"Idempotency-Key": key},
    )
    assert conflict.status_code == 409

    async with get_sessionmaker()() as session:
        rows = (
            await session.scalars(select(Incident).where(Incident.idempotency_key == key))
        ).all()

    assert len(rows) == 1
    assert str(rows[0].id) == first.json()["id"]
    assert rows[0].alert_title == "High latency on checkout"  # unchanged by the rejected payload


async def test_incident_persists_after_response(client: AsyncClient) -> None:
    key = _key()
    response = await client.post(
        "/api/v1/incidents", json=_payload(), headers={"Idempotency-Key": key}
    )
    assert response.status_code == 201
    incident_id = response.json()["id"]

    # Independent session/connection — proves the write actually committed, not just that
    # the in-process ORM object looked right before the transaction closed.
    async with get_sessionmaker()() as session:
        row = await session.scalar(select(Incident).where(Incident.id == incident_id))

    assert row is not None
    assert row.alert_title == "High latency on checkout"


async def test_get_incident_returns_created_incident(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/incidents",
        json=_payload(alert_description="p99 > 2s", external_incident_id="PD-123"),
        headers={"Idempotency-Key": _key()},
    )
    assert created.status_code == 201

    response = await client.get(f"/api/v1/incidents/{created.json()['id']}")

    assert response.status_code == 200
    assert response.json() == created.json()


async def test_get_incident_does_not_expose_internal_fields(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/incidents", json=_payload(), headers={"Idempotency-Key": _key()}
    )

    body = (await client.get(f"/api/v1/incidents/{created.json()['id']}")).json()

    assert "idempotency_key" not in body
    assert "request_fingerprint" not in body


async def test_get_unknown_incident_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/incidents/{uuid.uuid4()}")

    assert response.status_code == 404


async def test_get_incident_with_malformed_id_returns_422(client: AsyncClient) -> None:
    response = await client.get("/api/v1/incidents/not-a-uuid")

    assert response.status_code == 422


async def test_concurrent_requests_same_key_same_payload_resolve_to_one_row(
    client: AsyncClient,
) -> None:
    key = _key()
    payload = _payload()

    responses = await asyncio.gather(
        *(
            client.post("/api/v1/incidents", json=payload, headers={"Idempotency-Key": key})
            for _ in range(10)
        )
    )

    assert all(r.status_code == 201 for r in responses)
    ids = {r.json()["id"] for r in responses}
    assert len(ids) == 1

    async with get_sessionmaker()() as session:
        rows = (
            await session.scalars(select(Incident).where(Incident.idempotency_key == key))
        ).all()
    assert len(rows) == 1


async def test_concurrent_requests_same_key_different_payload_one_wins(
    client: AsyncClient,
) -> None:
    key = _key()

    responses = await asyncio.gather(
        *(
            client.post(
                "/api/v1/incidents",
                json=_payload(alert_title=f"alert {i}"),
                headers={"Idempotency-Key": key},
            )
            for i in range(10)
        )
    )

    statuses = [r.status_code for r in responses]
    assert statuses.count(201) == 1
    assert statuses.count(409) == 9

    async with get_sessionmaker()() as session:
        rows = (
            await session.scalars(select(Incident).where(Incident.idempotency_key == key))
        ).all()
    assert len(rows) == 1


async def test_unexpected_failure_after_insert_does_not_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_create(*args: object, **kwargs: object) -> tuple:
        await create_incident_idempotent(*args, **kwargs)
        raise RuntimeError("simulated failure after insert, before commit")

    monkeypatch.setattr(incident_service, "create_incident_idempotent", failing_create)

    key = _key()
    # raise_app_exceptions=False: observe the resulting response instead of having
    # httpx re-raise the forced server-side exception into this test.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/incidents", json=_payload(), headers={"Idempotency-Key": key}
        )
    assert response.status_code == 500

    async with get_sessionmaker()() as session:
        rows = (
            await session.scalars(select(Incident).where(Incident.idempotency_key == key))
        ).all()
    assert rows == []

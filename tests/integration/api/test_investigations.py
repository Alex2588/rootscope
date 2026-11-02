"""PostgreSQL integration tests for POST /api/v1/incidents/{incident_id}/investigations."""

import asyncio
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.db.models.investigation import Investigation
from app.db.session import get_sessionmaker
from app.domain.investigation import InvestigationStrategy
from app.main import app


def _key() -> str:
    return f"test-{uuid.uuid4()}"


def _url(incident_id: str) -> str:
    return f"/api/v1/incidents/{incident_id}/investigations"


@pytest_asyncio.fixture(loop_scope="session")
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture(loop_scope="session")
async def incident_id(client: AsyncClient) -> str:
    response = await client.post(
        "/api/v1/incidents",
        json={
            "alert_title": "High latency on checkout",
            "service_name": "checkout",
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-01T01:00:00Z",
        },
        headers={"Idempotency-Key": _key()},
    )
    assert response.status_code == 201
    return response.json()["id"]


async def _rows_for_key(key: str) -> list[Investigation]:
    async with get_sessionmaker()() as session:
        return list(
            await session.scalars(select(Investigation).where(Investigation.idempotency_key == key))
        )


@pytest.mark.parametrize("strategy", list(InvestigationStrategy))
async def test_create_investigation_returns_queued(
    client: AsyncClient, incident_id: str, strategy: InvestigationStrategy
) -> None:
    key = _key()
    response = await client.post(
        _url(incident_id), json={"strategy": strategy}, headers={"Idempotency-Key": key}
    )

    assert response.status_code == 202
    body = response.json()
    assert body["incident_id"] == incident_id
    assert body["strategy"] == strategy
    assert body["status"] == "queued"
    assert body["config"] == {}
    assert body["model"] is None
    assert body["report"] is None
    assert body["error"] is None
    assert body["started_at"] is None
    assert body["completed_at"] is None
    assert "idempotency_key" not in body
    assert "request_fingerprint" not in body

    rows = await _rows_for_key(key)
    assert [str(r.id) for r in rows] == [body["id"]]


async def test_replay_returns_same_investigation(client: AsyncClient, incident_id: str) -> None:
    key = _key()
    first = await client.post(
        _url(incident_id), json={"strategy": "llm_only"}, headers={"Idempotency-Key": key}
    )
    second = await client.post(
        _url(incident_id), json={"strategy": "llm_only"}, headers={"Idempotency-Key": key}
    )

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(await _rows_for_key(key)) == 1


async def test_same_key_different_strategy_returns_409(
    client: AsyncClient, incident_id: str
) -> None:
    key = _key()
    first = await client.post(
        _url(incident_id), json={"strategy": "llm_only"}, headers={"Idempotency-Key": key}
    )
    assert first.status_code == 202

    response = await client.post(
        _url(incident_id), json={"strategy": "agentic"}, headers={"Idempotency-Key": key}
    )

    assert response.status_code == 409
    rows = await _rows_for_key(key)
    assert [r.strategy for r in rows] == ["llm_only"]


async def test_same_key_on_different_incident_returns_409(
    client: AsyncClient, incident_id: str
) -> None:
    other = await client.post(
        "/api/v1/incidents",
        json={
            "alert_title": "Error spike on payments",
            "service_name": "payments",
            "window_start": "2026-01-02T00:00:00Z",
            "window_end": "2026-01-02T01:00:00Z",
        },
        headers={"Idempotency-Key": _key()},
    )
    assert other.status_code == 201
    key = _key()
    first = await client.post(
        _url(incident_id), json={"strategy": "llm_only"}, headers={"Idempotency-Key": key}
    )
    assert first.status_code == 202

    response = await client.post(
        _url(other.json()["id"]), json={"strategy": "llm_only"}, headers={"Idempotency-Key": key}
    )

    assert response.status_code == 409
    rows = await _rows_for_key(key)
    assert [str(r.incident_id) for r in rows] == [incident_id]


async def test_incident_can_have_multiple_investigations(
    client: AsyncClient, incident_id: str
) -> None:
    responses = [
        await client.post(
            _url(incident_id), json={"strategy": strategy}, headers={"Idempotency-Key": _key()}
        )
        for strategy in ("llm_only", "llm_only", "telemetry")
    ]

    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 3


async def test_unknown_incident_returns_404(client: AsyncClient) -> None:
    key = _key()
    response = await client.post(
        _url(str(uuid.uuid4())), json={"strategy": "llm_only"}, headers={"Idempotency-Key": key}
    )

    assert response.status_code == 404
    assert await _rows_for_key(key) == []


async def test_malformed_incident_id_returns_422(client: AsyncClient) -> None:
    response = await client.post(
        _url("not-a-uuid"), json={"strategy": "llm_only"}, headers={"Idempotency-Key": _key()}
    )

    assert response.status_code == 422


@pytest.mark.parametrize("body", [{"strategy": "v3"}, {}, {"strategy": "agentic", "config": {}}])
async def test_invalid_body_returns_422(client: AsyncClient, incident_id: str, body: dict) -> None:
    response = await client.post(_url(incident_id), json=body, headers={"Idempotency-Key": _key()})

    assert response.status_code == 422


async def test_missing_idempotency_key_returns_400(client: AsyncClient, incident_id: str) -> None:
    response = await client.post(_url(incident_id), json={"strategy": "llm_only"})

    assert response.status_code == 400


async def test_concurrent_requests_same_key_resolve_to_one_row(
    client: AsyncClient, incident_id: str
) -> None:
    key = _key()

    responses = await asyncio.gather(
        *(
            client.post(
                _url(incident_id), json={"strategy": "telemetry"}, headers={"Idempotency-Key": key}
            )
            for _ in range(10)
        )
    )

    assert all(r.status_code == 202 for r in responses)
    assert len({r.json()["id"] for r in responses}) == 1
    assert len(await _rows_for_key(key)) == 1


async def test_concurrent_conflicting_requests_one_strategy_wins(
    client: AsyncClient, incident_id: str
) -> None:
    key = _key()
    strategies = ["llm_only", "agentic"] * 5

    responses = await asyncio.gather(
        *(
            client.post(
                _url(incident_id), json={"strategy": strategy}, headers={"Idempotency-Key": key}
            )
            for strategy in strategies
        )
    )

    accepted = [r.json() for r in responses if r.status_code == 202]
    assert len(accepted) == 5
    assert sum(r.status_code == 409 for r in responses) == 5
    assert len({body["id"] for body in accepted}) == 1
    assert len({body["strategy"] for body in accepted}) == 1

    rows = await _rows_for_key(key)
    assert [(str(r.id), r.strategy) for r in rows] == [(accepted[0]["id"], accepted[0]["strategy"])]

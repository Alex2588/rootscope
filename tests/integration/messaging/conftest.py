import json
import os
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import aio_pika
import httpx
import pytest
import pytest_asyncio

from app.core.config import get_settings
from app.messaging.topology import (
    DEAD_LETTER_EXCHANGE,
    DEAD_LETTER_QUEUE,
    DELIVERY_LIMIT,
    EXCHANGE,
    EXECUTION_ROUTING_KEY,
    QUEUE,
)

_DEFINITIONS = Path(__file__).resolve().parents[3] / "infra" / "rabbitmq" / "definitions.local.json"
_MANAGEMENT_URL = os.environ.get("RABBITMQ_MANAGEMENT_URL", "http://localhost:15672")
_ADMIN_AUTH = (
    os.environ.get("RABBITMQ_ADMIN_USER", "admin"),
    os.environ.get("RABBITMQ_ADMIN_PASSWORD", "admin"),
)


def _name(value: str) -> str:
    return quote(value, safe="")


def _test_vhost() -> str:
    vhost = unquote(urlsplit(get_settings().rabbitmq_url).path.lstrip("/"))
    if not vhost.endswith("_test"):
        raise RuntimeError(
            f"Refusing destructive broker operations against vhost {vhost!r} — expected a "
            "name ending in '_test'. A pre-set RABBITMQ_URL may be overriding tests/conftest.py."
        )
    return vhost


def _definitions_for(vhost: str) -> dict:
    source = json.loads(_DEFINITIONS.read_text())

    def scoped(items: list[dict]) -> list[dict]:
        return [{**item, "vhost": vhost} for item in items]

    return {
        "vhosts": [{"name": vhost}],
        **{key: scoped(source[key]) for key in ("permissions", "exchanges", "queues", "bindings")},
    }


async def _topology_problems(client: httpx.AsyncClient, vhost: str) -> list[str]:
    problems: list[str] = []
    v = _name(vhost)

    async def fetch(path: str):
        response = await client.get(path)
        if response.status_code == 404:
            problems.append(f"missing: {path}")
            return None
        response.raise_for_status()
        return response.json()

    def expect(label: str, actual, expected) -> None:
        if actual != expected:
            problems.append(f"{label}: expected {expected!r}, got {actual!r}")

    if exchange := await fetch(f"/api/exchanges/{v}/{_name(EXCHANGE)}"):
        expect(f"{EXCHANGE} type", exchange["type"], "direct")
    if dlx := await fetch(f"/api/exchanges/{v}/{_name(DEAD_LETTER_EXCHANGE)}"):
        expect(f"{DEAD_LETTER_EXCHANGE} type", dlx["type"], "fanout")

    if queue := await fetch(f"/api/queues/{v}/{_name(QUEUE)}"):
        arguments = queue["arguments"]
        expect(f"{QUEUE} x-queue-type", arguments.get("x-queue-type"), "quorum")
        expect(f"{QUEUE} x-delivery-limit", arguments.get("x-delivery-limit"), DELIVERY_LIMIT)
        expect(
            f"{QUEUE} x-dead-letter-exchange",
            arguments.get("x-dead-letter-exchange"),
            DEAD_LETTER_EXCHANGE,
        )
    if dlq := await fetch(f"/api/queues/{v}/{_name(DEAD_LETTER_QUEUE)}"):
        expect(f"{DEAD_LETTER_QUEUE} x-queue-type", dlq["arguments"].get("x-queue-type"), "quorum")

    bindings = await fetch(f"/api/bindings/{v}/e/{_name(EXCHANGE)}/q/{_name(QUEUE)}")
    if bindings is not None and not any(
        b["routing_key"] == EXECUTION_ROUTING_KEY for b in bindings
    ):
        problems.append(f"no binding {EXCHANGE} -> {QUEUE} with key {EXECUTION_ROUTING_KEY!r}")
    dlx_bindings = await fetch(
        f"/api/bindings/{v}/e/{_name(DEAD_LETTER_EXCHANGE)}/q/{_name(DEAD_LETTER_QUEUE)}"
    )
    if dlx_bindings is not None and not dlx_bindings:
        problems.append(f"no binding {DEAD_LETTER_EXCHANGE} -> {DEAD_LETTER_QUEUE}")

    return problems


@pytest_asyncio.fixture(scope="session", loop_scope="session", autouse=True)
async def _rabbitmq_test_vhost() -> None:
    # A prerequisite for every messaging test: missing or drifted infrastructure fails here
    # with one clear message instead of as confusing publisher failures. Recreating the
    # vhost isolates tests from the dev stack and picks up definition changes.
    vhost = _test_vhost()
    try:
        async with httpx.AsyncClient(
            base_url=_MANAGEMENT_URL, auth=_ADMIN_AUTH, timeout=10
        ) as client:
            deleted = await client.delete(f"/api/vhosts/{_name(vhost)}")
            if deleted.status_code != 404:
                deleted.raise_for_status()
            imported = await client.post("/api/definitions", json=_definitions_for(vhost))
            imported.raise_for_status()
            problems = await _topology_problems(client, vhost)
    except httpx.HTTPError as exc:
        pytest.fail(
            f"RabbitMQ management API unavailable at {_MANAGEMENT_URL}: {exc!r}", pytrace=False
        )
    if problems:
        pytest.fail(
            "RabbitMQ topology is not provisioned as expected:\n" + "\n".join(problems),
            pytrace=False,
        )


@pytest_asyncio.fixture(loop_scope="session")
async def amqp_connection(_rabbitmq_test_vhost):
    # Non-robust on purpose: a broker failure during a test should surface, not be retried.
    async with await aio_pika.connect(get_settings().rabbitmq_url) as connection:
        yield connection


@pytest_asyncio.fixture(loop_scope="session")
async def confirm_exchange(amqp_connection):
    channel = await amqp_connection.channel(publisher_confirms=True, on_return_raises=True)
    return await channel.get_exchange(EXCHANGE, ensure=True)


@pytest_asyncio.fixture(loop_scope="session", autouse=True)
async def execution_queue(amqp_connection):
    channel = await amqp_connection.channel()
    queue = await channel.get_queue(QUEUE, ensure=True)
    await queue.purge()
    return queue

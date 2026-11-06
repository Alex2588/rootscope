import asyncio
import json
import uuid
from collections import Counter

import aio_pika
import pytest
from aio_pika.exceptions import ChannelInvalidStateError
from sqlalchemy import select

from app.db.models.outbox_event import OutboxEvent
from app.db.repositories.outbox import lock_unpublished_batch
from app.db.session import get_sessionmaker
from app.messaging.contracts import InvestigationExecutionRequested
from app.messaging.topology import EXCHANGE
from app.services.outbox_relay import publish_pending_batch

_EVENT_TYPE = InvestigationExecutionRequested.EVENT_TYPE


async def _insert_events(count: int, event_type: str = _EVENT_TYPE) -> list[uuid.UUID]:
    events = []
    for _ in range(count):
        investigation_id = uuid.uuid4()
        payload = InvestigationExecutionRequested(
            investigation_id=investigation_id, expected_attempt=1
        ).model_dump(mode="json")
        events.append(
            OutboxEvent(
                id=uuid.uuid4(),
                aggregate_id=investigation_id,
                event_type=event_type,
                payload=payload,
            )
        )
    async with get_sessionmaker()() as session, session.begin():
        session.add_all(events)
    return [event.id for event in events]


async def _events(ids: list[uuid.UUID]) -> dict[uuid.UUID, OutboxEvent]:
    async with get_sessionmaker()() as session:
        rows = await session.scalars(select(OutboxEvent).where(OutboxEvent.id.in_(ids)))
        return {row.id: row for row in rows}


async def _relay_batch(exchange, batch_size: int = 10) -> int:
    async with get_sessionmaker()() as session:
        return await publish_pending_batch(
            session, exchange, batch_size=batch_size, confirm_timeout=5
        )


async def _drain(queue, ids: list[uuid.UUID]) -> list[aio_pika.abc.AbstractIncomingMessage]:
    # Confirms arrive only after the broker has enqueued, so everything published is already
    # present; basic.get answers GetEmpty immediately, and the timeout bounds an unresponsive
    # broker (raised, not swallowed). Runs in the isolated test vhost.
    wanted = {str(i) for i in ids}
    messages = []
    while (message := await queue.get(no_ack=True, fail=False, timeout=5)) is not None:
        if message.message_id in wanted:
            messages.append(message)
    return messages


class _TimesOutFor:
    def __init__(self, exchange, routing_key: str) -> None:
        self._exchange = exchange
        self._routing_key = routing_key

    async def publish(self, message, routing_key: str, **kwargs):
        if routing_key == self._routing_key:
            raise TimeoutError
        return await self._exchange.publish(message, routing_key=routing_key, **kwargs)


async def test_confirmed_events_are_marked_published_and_delivered(
    confirm_exchange, execution_queue
) -> None:
    ids = await _insert_events(2)

    assert await _relay_batch(confirm_exchange) == 2

    events = await _events(ids)
    assert all(event.published_at is not None for event in events.values())
    messages = await _drain(execution_queue, ids)
    assert {uuid.UUID(m.message_id) for m in messages} == set(ids)
    for message in messages:
        event = events[uuid.UUID(message.message_id)]
        assert json.loads(message.body) == event.payload
        assert message.type == _EVENT_TYPE
        assert message.content_type == "application/json"
        assert message.delivery_mode == aio_pika.DeliveryMode.PERSISTENT


async def test_unroutable_event_stays_unpublished_while_batch_continues(
    confirm_exchange, execution_queue
) -> None:
    [routable] = await _insert_events(1)
    [unroutable] = await _insert_events(1, event_type="test.unroutable")

    assert await _relay_batch(confirm_exchange) == 2

    events = await _events([routable, unroutable])
    assert events[routable].published_at is not None
    assert events[unroutable].published_at is None
    delivered = await _drain(execution_queue, [routable, unroutable])
    assert [m.message_id for m in delivered] == [str(routable)]


async def test_simulated_publish_timeout_keeps_event_unpublished_and_raises(
    confirm_exchange, execution_queue
) -> None:
    # Simulates the client-side TimeoutError for one event; a real broker confirm timeout
    # can't be triggered deterministically. Verifies partial-batch handling only.
    [confirmed] = await _insert_events(1)
    [timed_out] = await _insert_events(1, event_type="test.timeout")

    with pytest.raises(TimeoutError):
        await _relay_batch(_TimesOutFor(confirm_exchange, "test.timeout"))

    events = await _events([confirmed, timed_out])
    assert events[confirmed].published_at is not None
    assert events[timed_out].published_at is None


async def test_concurrent_relays_lock_disjoint_rows() -> None:
    ids = await _insert_events(3)
    sessionmaker = get_sessionmaker()

    async with sessionmaker() as first, sessionmaker() as second:
        async with first.begin():
            locked_first = await lock_unpublished_batch(first, 2)
            async with second.begin():
                # Without SKIP LOCKED this would block on the first relay's locks.
                locked_second = await asyncio.wait_for(lock_unpublished_batch(second, 10), 5)

    first_ids = {event.id for event in locked_first}
    second_ids = {event.id for event in locked_second}
    assert len(first_ids) == 2
    assert len(second_ids) == 1
    assert first_ids.isdisjoint(second_ids)
    assert first_ids | second_ids == set(ids)


async def test_concurrent_relays_publish_each_event_once(amqp_connection, execution_queue) -> None:
    ids = await _insert_events(5)
    exchanges = []
    for _ in range(2):
        channel = await amqp_connection.channel(publisher_confirms=True, on_return_raises=True)
        exchanges.append(await channel.get_exchange(EXCHANGE, ensure=True))

    while sum(await asyncio.gather(*(_relay_batch(e, batch_size=2) for e in exchanges))):
        pass

    counts = Counter(m.message_id for m in await _drain(execution_queue, ids))
    assert counts == {str(i): 1 for i in ids}
    assert all(event.published_at is not None for event in (await _events(ids)).values())


async def test_closed_channel_marks_nothing_and_a_fresh_channel_recovers(
    amqp_connection, execution_queue
) -> None:
    [event_id] = await _insert_events(1)
    channel = await amqp_connection.channel(publisher_confirms=True, on_return_raises=True)
    stale_exchange = await channel.get_exchange(EXCHANGE, ensure=True)
    await channel.close()

    with pytest.raises(ChannelInvalidStateError):
        await _relay_batch(stale_exchange)
    assert (await _events([event_id]))[event_id].published_at is None

    fresh = await amqp_connection.channel(publisher_confirms=True, on_return_raises=True)
    assert await _relay_batch(await fresh.get_exchange(EXCHANGE, ensure=True)) == 1
    assert (await _events([event_id]))[event_id].published_at is not None

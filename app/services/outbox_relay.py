import asyncio
import json
import logging

import aio_pika
from aio_pika.abc import AbstractExchange
from aio_pika.exceptions import DeliveryError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.outbox_event import OutboxEvent
from app.db.repositories.outbox import lock_unpublished_batch, mark_published

logger = logging.getLogger(__name__)


def _to_message(event: OutboxEvent) -> aio_pika.Message:
    return aio_pika.Message(
        body=json.dumps(event.payload).encode(),
        message_id=str(event.id),
        type=event.event_type,
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )


async def publish_pending_batch(
    session: AsyncSession, exchange: AbstractExchange, *, batch_size: int, confirm_timeout: float
) -> int:
    """Publish one batch; returns the number of events locked."""
    async with session.begin():
        events = await lock_unpublished_batch(session, batch_size)
        if not events:
            return 0

        # Concurrent publishes bound the time row locks are held to one confirm timeout.
        results = await asyncio.gather(
            *(
                exchange.publish(
                    _to_message(event),
                    routing_key=event.event_type,
                    mandatory=True,
                    timeout=confirm_timeout,
                )
                for event in events
            ),
            return_exceptions=True,
        )

        confirmed = []
        broker_failure: BaseException | None = None
        for event, result in zip(events, results, strict=True):
            if isinstance(result, DeliveryError):
                # Nacked or returned unroutable: this event stays unpublished.
                logger.warning("Outbox event %s not delivered: %r", event.id, result)
            elif isinstance(result, BaseException):
                # Timeout or channel failure: outcome unknown, so the event stays unpublished
                # and may be published again; consumers tolerate duplicates.
                broker_failure = broker_failure or result
            else:
                confirmed.append(event.id)

        if confirmed:
            await mark_published(session, confirmed)

    if broker_failure is not None:
        raise broker_failure
    return len(events)

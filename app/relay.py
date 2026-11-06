import asyncio
import logging
import random
from collections.abc import Awaitable, Callable

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractConnection, AbstractExchange
from aio_pika.exceptions import ChannelNotFoundEntity

from app.core.config import Settings, get_settings
from app.db.session import get_sessionmaker
from app.messaging.topology import EXCHANGE
from app.services.outbox_relay import publish_pending_batch

logger = logging.getLogger(__name__)

ConnectionFactory = Callable[[str], Awaitable[AbstractConnection]]


class TopologyMissingError(RuntimeError):
    pass


async def _sleep(seconds: float, stop: asyncio.Event) -> None:
    try:
        await asyncio.wait_for(stop.wait(), seconds)
    except TimeoutError:
        pass


async def _open_exchange(
    connection: AbstractConnection,
) -> tuple[AbstractChannel, AbstractExchange]:
    channel = await connection.channel(publisher_confirms=True, on_return_raises=True)
    try:
        return channel, await channel.get_exchange(EXCHANGE, ensure=True)
    except ChannelNotFoundEntity as exc:
        # Topology is provisioned externally: a missing exchange is a configuration
        # failure to surface, not something to retry or recreate.
        raise TopologyMissingError(f"Exchange {EXCHANGE!r} is not provisioned") from exc


async def _publish_until_broken(
    connection: AbstractConnection,
    channel: AbstractChannel,
    exchange: AbstractExchange,
    settings: Settings,
    stop: asyncio.Event,
) -> bool:
    """Publish until the broker connection or channel closes; returns whether any batch
    completed, i.e. whether the connection proved healthy."""
    sessionmaker = get_sessionmaker()
    healthy = False
    while not stop.is_set():
        try:
            async with sessionmaker() as session:
                locked = await publish_pending_batch(
                    session,
                    exchange,
                    batch_size=settings.relay_batch_size,
                    confirm_timeout=settings.relay_confirm_timeout_seconds,
                )
            healthy = True
        except Exception:
            logger.exception("Outbox relay batch failed")
            if connection.is_closed or channel.is_closed:
                return healthy
            locked = 0
        # A full batch suggests a backlog, so continue without sleeping.
        if locked < settings.relay_batch_size:
            await _sleep(settings.relay_poll_interval_seconds, stop)
    return healthy


async def run(
    settings: Settings | None = None,
    connect: ConnectionFactory = aio_pika.connect,
    stop: asyncio.Event | None = None,
) -> None:
    settings = settings or get_settings()
    stop = stop or asyncio.Event()
    delay = settings.relay_poll_interval_seconds

    while not stop.is_set():
        healthy = False
        try:
            async with await connect(settings.rabbitmq_url) as connection:
                channel, exchange = await _open_exchange(connection)
                healthy = await _publish_until_broken(connection, channel, exchange, settings, stop)
        except TopologyMissingError:
            raise
        except Exception:
            logger.exception("RabbitMQ connection failed")

        if stop.is_set():
            break
        # Reset only after a connection proved healthy, so connections that fail right
        # after opening still back off.
        if healthy:
            delay = settings.relay_poll_interval_seconds
        await _sleep(random.uniform(delay / 2, delay), stop)
        delay = min(delay * 2, settings.relay_reconnect_max_delay_seconds)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())

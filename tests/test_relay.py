import asyncio

import pytest
from aio_pika.exceptions import (
    AMQPConnectionError,
    ChannelInvalidStateError,
    ChannelNotFoundEntity,
)

from app import relay
from app.core.config import Settings


class _UnexpectedConnectAttempt(BaseException):
    # BaseException so the relay's `except Exception` retry loop can't swallow it.
    pass


class _FakeChannel:
    def __init__(self, missing_exchange: bool = False) -> None:
        self.is_closed = False
        self._missing_exchange = missing_exchange
        self.exchange = object()

    async def get_exchange(self, name: str, *, ensure: bool):
        if self._missing_exchange:
            raise ChannelNotFoundEntity()
        return self.exchange


class _FakeConnection:
    def __init__(self, channel: _FakeChannel) -> None:
        self.is_closed = False
        self._channel = channel

    async def channel(self, **kwargs) -> _FakeChannel:
        return self._channel

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc) -> None:
        self.is_closed = True


def _settings() -> Settings:
    return Settings(
        relay_poll_interval_seconds=0.01,
        relay_reconnect_max_delay_seconds=0.02,
        relay_batch_size=10,
    )


def _factory(outcomes: list):
    calls: list[str] = []

    async def connect(url: str):
        if len(calls) >= len(outcomes):
            raise _UnexpectedConnectAttempt(f"connection attempt {len(calls) + 1} not expected")
        outcome = outcomes[len(calls)]
        calls.append(url)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    return connect, calls


async def test_relay_retries_initial_connection_and_reopens_after_channel_loss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = _FakeChannel(), _FakeChannel()
    connect, calls = _factory(
        [AMQPConnectionError(), _FakeConnection(first), _FakeConnection(second)]
    )
    stop = asyncio.Event()
    published_with = []

    async def fake_publish(session, exchange, **kwargs) -> int:
        published_with.append(exchange)
        if exchange is first.exchange:
            first.is_closed = True
            raise ChannelInvalidStateError("channel closed")
        stop.set()
        return 0

    monkeypatch.setattr(relay, "publish_pending_batch", fake_publish)
    await asyncio.wait_for(relay.run(_settings(), connect, stop), 5)

    assert len(calls) == 3
    assert published_with == [first.exchange, second.exchange]


async def test_relay_keeps_connection_when_batch_fails_with_channel_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connect, calls = _factory([_FakeConnection(_FakeChannel())])
    stop = asyncio.Event()
    attempts = 0

    async def fake_publish(session, exchange, **kwargs) -> int:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database unavailable")
        stop.set()
        return 0

    monkeypatch.setattr(relay, "publish_pending_batch", fake_publish)
    await asyncio.wait_for(relay.run(_settings(), connect, stop), 5)

    assert len(calls) == 1
    assert attempts == 2


async def test_relay_fails_fast_when_exchange_is_missing() -> None:
    connect, calls = _factory([_FakeConnection(_FakeChannel(missing_exchange=True))])

    with pytest.raises(relay.TopologyMissingError):
        await asyncio.wait_for(relay.run(_settings(), connect, asyncio.Event()), 5)
    assert len(calls) == 1


async def test_backoff_grows_across_connections_that_never_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channels = [_FakeChannel() for _ in range(3)]
    connect, _ = _factory([_FakeConnection(channel) for channel in channels])
    stop = asyncio.Event()
    sleeps: list[float] = []
    real_sleep = relay._sleep

    async def recording_sleep(seconds: float, stop_event: asyncio.Event) -> None:
        sleeps.append(seconds)
        await real_sleep(0, stop_event)

    async def fake_publish(session, exchange, **kwargs) -> int:
        channel = next(c for c in channels if c.exchange is exchange)
        channel.is_closed = True
        if channel is channels[-1]:
            stop.set()
        raise ChannelInvalidStateError("channel closed")

    monkeypatch.setattr(relay, "_sleep", recording_sleep)
    monkeypatch.setattr(relay, "publish_pending_batch", fake_publish)
    await asyncio.wait_for(relay.run(_settings(), connect, stop), 5)

    # Jittered within [delay/2, delay]: 0.01 then the 0.02 cap — never reset to the minimum.
    assert len(sleeps) == 2
    assert 0.005 <= sleeps[0] <= 0.01
    assert 0.01 <= sleeps[1] <= 0.02

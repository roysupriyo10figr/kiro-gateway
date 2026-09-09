"""Keepalive lifecycle tests, isolated from network activity."""

import asyncio
from typing import AsyncGenerator

import pytest

from kiro.streaming_keepalive import with_keepalive


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "heartbeat", ['event: ping\ndata: {"type":"ping"}\n\n', ": ping\n\n"]
)
async def test_idle_read_survives_multiple_heartbeats(heartbeat: str) -> None:
    """Both protocols preserve a pending read and subsequent event ordering."""
    ready = asyncio.Event()
    closed = asyncio.Event()

    async def source() -> AsyncGenerator[str, None]:
        try:
            await ready.wait()
            yield "first"
            yield "last"
        finally:
            closed.set()

    stream = with_keepalive(source(), heartbeat, interval=0.001)
    assert await anext(stream) == heartbeat
    assert await anext(stream) == heartbeat
    assert not closed.is_set()
    ready.set()
    assert [chunk async for chunk in stream] == ["first", "last"]
    assert closed.is_set()


@pytest.mark.asyncio
async def test_disconnect_cancels_read_and_closes_source() -> None:
    """Closing a client stream does not leave an upstream read running."""
    closed = asyncio.Event()

    async def source() -> AsyncGenerator[str, None]:
        try:
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            closed.set()

    stream = with_keepalive(source(), "ping", interval=0.001)
    assert await anext(stream) == "ping"
    await stream.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
async def test_cancellation_closes_source() -> None:
    """Cancellation while waiting cleans up the pending source task."""
    started, closed = asyncio.Event(), asyncio.Event()

    async def source() -> AsyncGenerator[str, None]:
        try:
            started.set()
            await asyncio.Event().wait()
            yield "unreachable"
        finally:
            closed.set()

    stream = with_keepalive(source(), "ping")
    task = asyncio.create_task(anext(stream))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("interval", [0, 0.001])
async def test_source_errors_propagate(interval: float) -> None:
    """Keepalive wrapping cannot turn an upstream failure into success."""

    async def source() -> AsyncGenerator[str, None]:
        yield "first"
        raise RuntimeError("upstream failed")

    stream = with_keepalive(source(), "ping", interval)
    assert await anext(stream) == "first"
    with pytest.raises(RuntimeError, match="upstream failed"):
        await anext(stream)


@pytest.mark.asyncio
async def test_empty_source_has_no_extra_events() -> None:
    """A finished stream terminates without trailing keepalives."""

    async def source() -> AsyncGenerator[str, None]:
        for chunk in []:
            yield chunk

    assert [chunk async for chunk in with_keepalive(source(), "ping")] == []


@pytest.mark.asyncio
async def test_negative_interval_rejected() -> None:
    """Reject invalid timing configuration before starting a source read."""

    async def source() -> AsyncGenerator[str, None]:
        yield "first"

    with pytest.raises(ValueError, match="zero or positive"):
        await anext(with_keepalive(source(), "ping", -1))

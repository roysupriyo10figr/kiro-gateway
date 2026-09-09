"""Keep downstream SSE connections active while upstream generation is quiet."""

import asyncio
from contextlib import suppress
from typing import AsyncGenerator, Optional

from loguru import logger


async def with_keepalive(
    source: AsyncGenerator[str, None],
    heartbeat: str,
    interval: float = 10.0,
) -> AsyncGenerator[str, None]:
    """Interleave keepalives without cancelling or restarting upstream reads.

    Args:
        source: Owned event generator, closed on completion or disconnect.
        heartbeat: Protocol-specific SSE ping or comment.
        interval: Seconds of downstream silence before a keepalive; zero disables.

    Yields:
        Original events in order, with keepalives during idle periods.

    Raises:
        ValueError: If the interval is negative.
        Exception: Source failures propagate unchanged to the response owner.
    """
    if interval < 0:
        raise ValueError("Keepalive interval must be zero or positive")
    pending: Optional[asyncio.Task] = None
    try:
        if interval == 0:
            async for chunk in source:
                yield chunk
            return
        while True:
            if pending is None:
                pending = asyncio.create_task(source.__anext__())
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                logger.debug("Sending SSE keepalive while upstream is quiet")
                yield heartbeat
                continue
            completed, pending = pending, None
            try:
                chunk = completed.result()
            except StopAsyncIteration:
                return
            yield chunk
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await pending
        await source.aclose()

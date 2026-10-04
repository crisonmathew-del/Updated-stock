"""Helpers for long-running service processes (scheduler, streamer)."""

import asyncio
import contextlib
import signal


def install_stop_signals() -> asyncio.Event:
    """Return an event that is set on SIGINT/SIGTERM so services can shut down cleanly."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    return stop


async def sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    """Sleep for `seconds`, returning early if `stop` is set."""
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout=seconds)

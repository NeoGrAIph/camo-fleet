"""Shared helpers for managing subprocess I/O and lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from asyncio import subprocess as aio_subprocess
from typing import Iterable

LOGGER = logging.getLogger(__name__)


async def drain_stream(stream: asyncio.StreamReader | None, prefix: str) -> None:
    """Continuously read from *stream* to avoid blocking pipes and log output."""

    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            break
        LOGGER.debug("%s: %s", prefix, line.decode().rstrip())


async def terminate_process(process: aio_subprocess.Process, *, kill: bool = False) -> None:
    """Terminate *process* gracefully, falling back to kill on timeout."""

    if process.returncode is not None:
        return
    if not kill:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
            return
        except TimeoutError:
            LOGGER.warning("Process %s did not exit after terminate; killing", process.pid)
    process.kill()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


async def cancel_tasks(tasks: Iterable[asyncio.Task[None]]) -> None:
    """Cancel and await completion of background tasks."""

    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


__all__ = ["cancel_tasks", "drain_stream", "terminate_process"]

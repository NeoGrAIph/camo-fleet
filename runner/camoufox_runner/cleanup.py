"""Utilities responsible for idle session cleanup in the Camoufox runner."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Iterable

LOGGER = logging.getLogger(__name__)


class IdleTimeoutEvaluator:
    """Determine which sessions exceeded their idle timeout."""

    def __init__(self, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.time

    def select_expired(self, handles: Iterable["SessionHandle"]) -> list["SessionHandle"]:
        now = self._clock()
        expired: list["SessionHandle"] = []
        for handle in handles:
            ttl_deadline = handle.last_seen_at.timestamp() + handle.idle_ttl_seconds
            if now >= ttl_deadline:
                expired.append(handle)
        return expired


class CleanupScheduler:
    """Run cleanup callbacks on a fixed interval."""

    def __init__(
        self,
        *,
        interval: float,
        callback: Callable[[], Awaitable[None]],
        name: str = "camoufox-cleanup",
    ) -> None:
        self._interval = interval
        self._callback = callback
        self._task: asyncio.Task[None] | None = None
        self._name = name

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name=self._name)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            try:
                await self._callback()
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("Cleanup callback failed: %s", exc)


__all__ = ["CleanupScheduler", "IdleTimeoutEvaluator"]

"""Background cleanup helpers for session TTL enforcement."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any

LOGGER = logging.getLogger(__name__)


class IdleSessionCleaner:
    """Run periodic cleanup tasks to reap expired sessions."""

    def __init__(
        self,
        *,
        interval: float,
        collect_expired: Callable[[], Awaitable[list[Any]]],
        on_expired: Callable[[Any], Awaitable[None]],
        logger: logging.Logger | None = None,
    ) -> None:
        self._interval = interval
        self._collect_expired = collect_expired
        self._on_expired = on_expired
        self._logger = logger or LOGGER
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop(), name="camoufox-cleanup")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def run_once(self) -> None:
        try:
            handles = await self._collect_expired()
        except Exception as exc:  # pragma: no cover - defensive
            self._logger.warning("Failed to collect expired sessions: %s", exc)
            return
        for handle in handles:
            try:
                await self._on_expired(handle)
            except Exception as exc:  # pragma: no cover - defensive
                session_id = getattr(handle, "id", "<unknown>")
                self._logger.warning("Failed to clean up session %s: %s", session_id, exc)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self.run_once()


__all__ = ["IdleSessionCleaner"]

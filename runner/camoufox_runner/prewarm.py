"""Management of prewarmed browser and VNC resources."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from .browser import BrowserLauncher, SubprocessBrowserServer
from .vnc import VncProcessManager, VncSession

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PrewarmedResource:
    """Container holding a prewarmed browser server and optional VNC session."""

    server: SubprocessBrowserServer
    vnc_session: VncSession | None
    headless: bool


class PrewarmPool:
    """Maintain reusable browser servers for faster session start-up."""

    def __init__(
        self,
        *,
        launcher: BrowserLauncher,
        vnc_manager: VncProcessManager,
        headless_target: int,
        vnc_target: int,
        check_interval: float,
        logger: logging.Logger | None = None,
    ) -> None:
        self._launcher = launcher
        self._vnc_manager = vnc_manager
        self._headless_target = max(0, headless_target)
        self._vnc_target = max(0, vnc_target if vnc_manager.available else 0)
        self._check_interval = check_interval
        self._logger = logger or LOGGER
        self._headless: list[PrewarmedResource] = []
        self._vnc: list[PrewarmedResource] = []
        self._lock = asyncio.Lock()
        self._top_up_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._closing = False

    async def start(self) -> None:
        """Start the background loop that keeps the pool filled."""

        await self.top_up_once()
        if self._should_run_loop():
            self._task = asyncio.create_task(self._loop(), name="camoufox-prewarm")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def close(self) -> None:
        """Stop the background loop and dispose prewarmed resources."""

        self._closing = True
        try:
            await self.stop()
            async with self._top_up_lock:
                async with self._lock:
                    resources = list(self._headless) + list(self._vnc)
                    self._headless.clear()
                    self._vnc.clear()
            for item in resources:
                try:
                    await item.server.close()
                finally:
                    await self._vnc_manager.stop_session(item.vnc_session)
        finally:
            self._closing = False

    async def acquire(self, *, vnc: bool, headless: bool) -> PrewarmedResource | None:
        async with self._lock:
            if vnc and self._vnc:
                return self._vnc.pop()
            if (not vnc) and headless and self._headless:
                return self._headless.pop()
            return None

    def schedule_top_up(self) -> None:
        if self._closing or not self._should_run_loop():
            return
        task = asyncio.create_task(self.top_up_once(), name="camoufox-prewarm-kick")
        task.add_done_callback(lambda _: None)

    async def top_up_once(self) -> None:
        async with self._top_up_lock:
            if self._closing:
                return
            need_headless: int
            need_vnc: int
            async with self._lock:
                need_headless = max(0, self._headless_target - len(self._headless))
                need_vnc = max(0, self._vnc_target - len(self._vnc))
            for _ in range(need_headless):
                if self._closing:
                    break
                try:
                    server = await self._launcher.launch(headless=True, vnc=False, display=None)
                    resource = PrewarmedResource(server=server, vnc_session=None, headless=True)
                    async with self._lock:
                        self._headless.append(resource)
                except Exception as exc:  # pragma: no cover - defensive
                    self._logger.warning("Failed to prewarm headless server: %s", exc)
                    break
            for _ in range(need_vnc):
                vnc_session: VncSession | None = None
                if self._closing:
                    break
                try:
                    vnc_session = await self._vnc_manager.start_session()
                    server = await self._launcher.launch(
                        headless=False,
                        vnc=True,
                        display=vnc_session.display,
                    )
                    resource = PrewarmedResource(server=server, vnc_session=vnc_session, headless=False)
                    async with self._lock:
                        self._vnc.append(resource)
                except Exception as exc:  # pragma: no cover - defensive
                    self._logger.warning("Failed to prewarm VNC server: %s", exc)
                    if vnc_session is not None:
                        with contextlib.suppress(Exception):
                            await self._vnc_manager.stop_session(vnc_session)
                    break

    def _should_run_loop(self) -> bool:
        return self._headless_target > 0 or self._vnc_target > 0

    async def _loop(self) -> None:
        while True:
            try:
                await self.top_up_once()
            except Exception as exc:  # pragma: no cover - defensive
                self._logger.warning("Prewarm loop error: %s", exc)
            await asyncio.sleep(self._check_interval)


__all__ = ["PrewarmPool", "PrewarmedResource"]

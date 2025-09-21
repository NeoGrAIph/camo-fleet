"""Prewarm pool management for Camoufox browser sessions."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass

from .config import RunnerSettings
from .playwright_control import BrowserServerHandle, BrowserServerLauncher
from .vnc_controller import VncProcessManager, VncSession

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class PrewarmedResource:
    """A ready-to-use bundle containing a browser server and optional VNC session."""

    server: BrowserServerHandle
    vnc_session: VncSession | None
    headless: bool


class PrewarmPool:
    """Maintain a pool of prewarmed browser servers to reduce cold-start latency."""

    def __init__(
        self,
        settings: RunnerSettings,
        browser_launcher: BrowserServerLauncher,
        vnc_manager: VncProcessManager,
    ) -> None:
        self._settings = settings
        self._browser_launcher = browser_launcher
        self._vnc_manager = vnc_manager
        self._headless_target = settings.prewarm_headless
        self._vnc_target = settings.prewarm_vnc if vnc_manager.is_available else 0
        if settings.prewarm_vnc > 0 and not vnc_manager.is_available:
            LOGGER.info("VNC tooling unavailable; disabling VNC prewarm")
        self._check_interval = settings.prewarm_check_interval_seconds
        self._headless: list[PrewarmedResource] = []
        self._vnc: list[PrewarmedResource] = []
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the background maintainer and perform an initial top up."""

        if not self._requires_background_loop:
            return
        await self.top_up_once()
        self._task = asyncio.create_task(self._run(), name="camoufox-prewarm")

    async def close(self) -> None:
        """Stop background work and drain all prewarmed resources."""

        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        await self.drain()

    async def drain(self) -> None:
        async with self._lock:
            resources = list(self._headless) + list(self._vnc)
            self._headless.clear()
            self._vnc.clear()
        for item in resources:
            try:
                await item.server.close()
            finally:
                await self._vnc_manager.stop_session(item.vnc_session)

    async def acquire(self, *, vnc: bool, headless: bool) -> PrewarmedResource | None:
        async with self._lock:
            if vnc and self._vnc:
                return self._vnc.pop()
            if (not vnc) and headless and self._headless:
                return self._headless.pop()
            return None

    def request_top_up(self) -> None:
        if not self._requires_background_loop:
            return
        task = asyncio.create_task(self.top_up_once(), name="camoufox-prewarm-kick")
        task.add_done_callback(lambda _: None)

    async def top_up_once(self) -> None:
        async with self._lock:
            need_headless = max(0, self._headless_target - len(self._headless))
            need_vnc = max(0, self._vnc_target - len(self._vnc))
        for _ in range(need_headless):
            try:
                server = await self._browser_launcher.launch(
                    headless=True,
                    vnc=False,
                    display=None,
                    override_proxy=None,
                )
                item = PrewarmedResource(server=server, vnc_session=None, headless=True)
                async with self._lock:
                    self._headless.append(item)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("Failed to prewarm headless server: %s", exc)
                break
        for _ in range(need_vnc):
            vnc_session: VncSession | None = None
            try:
                vnc_session = await self._vnc_manager.start_session()
                server = await self._browser_launcher.launch(
                    headless=False,
                    vnc=True,
                    display=vnc_session.display,
                    override_proxy=None,
                )
                item = PrewarmedResource(server=server, vnc_session=vnc_session, headless=False)
                async with self._lock:
                    self._vnc.append(item)
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("Failed to prewarm VNC server: %s", exc)
                if vnc_session is not None:
                    with contextlib.suppress(Exception):
                        await self._vnc_manager.stop_session(vnc_session)
                break

    async def _run(self) -> None:
        while True:
            try:
                await self.top_up_once()
            except Exception as exc:  # pragma: no cover - defensive
                LOGGER.warning("Prewarm loop error: %s", exc)
            await asyncio.sleep(self._check_interval)

    @property
    def _requires_background_loop(self) -> bool:
        return self._headless_target > 0 or self._vnc_target > 0


__all__ = ["PrewarmPool", "PrewarmedResource"]

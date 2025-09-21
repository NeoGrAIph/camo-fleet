"""Session orchestration built on top of dedicated lifecycle components.

The session manager coordinates four specialised helpers:
* :mod:`camoufox_runner.browser` launches Playwright browser servers.
* :mod:`camoufox_runner.vnc` manages the Xvfb/x11vnc/websockify toolchain.
* :mod:`camoufox_runner.prewarm` keeps warm pools of ready-to-use resources.
* :mod:`camoufox_runner.cleanup` enforces per-session idle TTL values.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from playwright.async_api import Playwright

from .browser import BrowserLauncher, SubprocessBrowserServer
from .cleanup import IdleSessionCleaner
from .config import RunnerSettings
from .models import SessionDetail, SessionStatus, SessionSummary
from .prewarm import PrewarmPool, PrewarmedResource
from .vnc import VNCUnavailableError, VncProcessManager, VncSession, build_vnc_payload

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionHandle:
    """In-memory representation of an active session."""

    id: str
    headless: bool
    idle_ttl_seconds: int
    created_at: datetime
    last_seen_at: datetime
    server: SubprocessBrowserServer
    vnc: bool
    start_url: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    status: SessionStatus = SessionStatus.INIT
    controller_browser: Any | None = None
    controller_context: Any | None = None
    controller_page: Any | None = None
    vnc_session: VncSession | None = field(default=None, repr=False)
    start_url_wait: str = "load"

    def summary(self) -> SessionSummary:
        return SessionSummary(
            id=self.id,
            status=self.status,
            created_at=self.created_at,
            last_seen_at=self.last_seen_at,
            headless=self.headless,
            idle_ttl_seconds=self.idle_ttl_seconds,
            labels=self.labels,
            vnc=self.vnc,
            start_url_wait=self.start_url_wait,
        )

    def detail(self, ws_endpoint: str, vnc_payload: dict[str, Any]) -> SessionDetail:
        return SessionDetail(
            **self.summary().model_dump(),
            ws_endpoint=ws_endpoint,
            vnc_info=vnc_payload,
        )


class SessionManager:
    """Manage lifecycle of Camoufox sessions and supporting background tasks."""

    def __init__(self, settings: RunnerSettings, playwright: Playwright) -> None:
        self._settings = settings
        self._playwright = playwright
        self._sessions: dict[str, SessionHandle] = {}
        self._lock = asyncio.Lock()
        self._browser_launcher = BrowserLauncher(logger=LOGGER)
        self._vnc_manager = VncProcessManager(settings, logger=LOGGER)
        self._prewarm = PrewarmPool(
            launcher=self._browser_launcher,
            vnc_manager=self._vnc_manager,
            headless_target=settings.prewarm_headless,
            vnc_target=settings.prewarm_vnc,
            check_interval=settings.prewarm_check_interval_seconds,
            logger=LOGGER,
        )
        self._cleanup = IdleSessionCleaner(
            interval=settings.cleanup_interval,
            collect_expired=self._collect_expired_sessions,
            on_expired=self._shutdown_handle,
            logger=LOGGER,
        )
        self._start_url_wait = settings.start_url_wait
        self._bootstrap_tasks: set[asyncio.Task[None]] = set()

    async def start(self) -> None:
        """Start background workers that clean up expired sessions and prewarm pools."""

        self._cleanup.start()
        await self._prewarm.start()

    async def close(self) -> None:
        """Stop background workers and terminate all active/prewarmed sessions."""

        await self._cleanup.stop()
        if self._bootstrap_tasks:
            tasks = list(self._bootstrap_tasks)
            self._bootstrap_tasks.clear()
            for task in tasks:
                task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.gather(*tasks, return_exceptions=True)
        await self._prewarm.close()
        await self._close_all()

    async def _close_all(self) -> None:
        async with self._lock:
            handles = list(self._sessions.values())
            self._sessions.clear()
        for handle in handles:
            await self._shutdown_handle(handle)

    async def list_summaries(self) -> list[SessionSummary]:
        async with self._lock:
            return [handle.summary() for handle in self._sessions.values()]

    async def list_details(self) -> list[SessionDetail]:
        async with self._lock:
            handles = list(self._sessions.values())
        return [self.detail_for(handle) for handle in handles]

    async def get(self, session_id: str) -> SessionHandle | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def create(self, payload: dict[str, Any]) -> SessionHandle:
        defaults = self._settings.session_defaults
        headless = payload.get("headless")
        if headless is None:
            headless = defaults.headless
        vnc_enabled = bool(payload.get("vnc", False))
        proxy_override = payload.get("proxy") or None
        vnc_session: VncSession | None = None
        if vnc_enabled:
            headless = False
            if not self._vnc_manager.available:
                raise VNCUnavailableError("VNC is not supported on this runner")
        prewarmed: PrewarmedResource | None = None
        if not proxy_override:
            prewarmed = await self._prewarm.acquire(vnc=vnc_enabled, headless=headless)
        idle_ttl = payload.get("idle_ttl_seconds") or defaults.idle_ttl_seconds
        labels = payload.get("labels") or {}
        start_url = payload.get("start_url") or defaults.start_url
        wait_override = payload.get("start_url_wait")
        if wait_override in {"none", "domcontentloaded", "load"}:
            start_url_wait = wait_override
        else:
            start_url_wait = self._start_url_wait

        try:
            if prewarmed is not None:
                server = prewarmed.server
                vnc_session = prewarmed.vnc_session
            else:
                if vnc_enabled:
                    vnc_session = await self._vnc_manager.start_session()
                server = await self._browser_launcher.launch(
                    headless=headless,
                    vnc=vnc_enabled,
                    display=vnc_session.display if vnc_session else None,
                    override_proxy=proxy_override,
                )
        except Exception:
            await self._vnc_manager.stop_session(vnc_session)
            raise

        created_at = datetime.now(tz=UTC)
        handle = SessionHandle(
            id=str(uuid.uuid4()),
            headless=headless,
            idle_ttl_seconds=idle_ttl,
            created_at=created_at,
            last_seen_at=created_at,
            server=server,
            vnc=vnc_enabled,
            start_url=start_url,
            labels=labels,
            status=SessionStatus.READY,
            vnc_session=vnc_session,
            start_url_wait=start_url_wait,
        )
        self._schedule_bootstrap(handle)
        async with self._lock:
            self._sessions[handle.id] = handle
        self._prewarm.schedule_top_up()
        return handle

    async def delete(self, session_id: str) -> SessionHandle | None:
        async with self._lock:
            handle = self._sessions.pop(session_id, None)
        if handle:
            handle.status = SessionStatus.TERMINATING
            await self._shutdown_handle(handle)
        return handle

    async def touch(self, session_id: str) -> SessionHandle | None:
        async with self._lock:
            handle = self._sessions.get(session_id)
            if not handle:
                return None
            handle.last_seen_at = datetime.now(tz=UTC)
            return handle

    async def iter_details(self):
        async with self._lock:
            handles = list(self._sessions.values())
        for handle in handles:
            yield self.detail_for(handle)

    def ws_endpoint_for(self, handle: SessionHandle) -> str:
        return handle.server.ws_endpoint

    def detail_for(self, handle: SessionHandle) -> SessionDetail:
        return handle.detail(
            self.ws_endpoint_for(handle),
            build_vnc_payload(handle.vnc_session, enabled=handle.vnc),
        )

    async def _collect_expired_sessions(self) -> list[SessionHandle]:
        now = time.time()
        stale: list[SessionHandle] = []
        async with self._lock:
            for handle in list(self._sessions.values()):
                ttl_deadline = handle.last_seen_at.timestamp() + handle.idle_ttl_seconds
                if now >= ttl_deadline:
                    handle.status = SessionStatus.TERMINATING
                    stale.append(handle)
                    self._sessions.pop(handle.id, None)
        for handle in stale:
            LOGGER.info("Session %s expired — shutting down", handle.id)
        return stale

    async def _shutdown_handle(self, handle: SessionHandle) -> None:
        await self._teardown_controller(handle)
        try:
            await handle.server.close()
        finally:
            await self._vnc_manager.stop_session(handle.vnc_session)
            handle.vnc_session = None
            handle.status = SessionStatus.DEAD

    def _schedule_bootstrap(self, handle: SessionHandle) -> None:
        if not handle.start_url:
            return
        if handle.start_url_wait == "none":
            return

        task = asyncio.create_task(
            self._bootstrap_session(handle),
            name=f"camoufox-bootstrap:{handle.id}",
        )
        self._bootstrap_tasks.add(task)

        def _cleanup(_: asyncio.Future[Any]) -> None:
            self._bootstrap_tasks.discard(task)

        task.add_done_callback(_cleanup)

    async def _bootstrap_session(self, handle: SessionHandle) -> None:
        if not handle.start_url:
            return
        if handle.start_url_wait == "none":
            return
        try:
            browser = await self._playwright.firefox.connect(handle.server.ws_endpoint)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(handle.start_url, wait_until=handle.start_url_wait)
            handle.controller_browser = browser
            handle.controller_context = context
            handle.controller_page = page
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("Failed to open %s in session %s: %s", handle.start_url, handle.id, exc)

    async def _teardown_controller(self, handle: SessionHandle) -> None:
        if handle.controller_page:
            with contextlib.suppress(Exception):
                await handle.controller_page.close()
            handle.controller_page = None
        if handle.controller_context:
            with contextlib.suppress(Exception):
                await handle.controller_context.close()
            handle.controller_context = None
        if handle.controller_browser:
            with contextlib.suppress(Exception):
                await handle.controller_browser.close()
            handle.controller_browser = None


__all__ = ["SessionManager", "SessionHandle", "VNCUnavailableError"]

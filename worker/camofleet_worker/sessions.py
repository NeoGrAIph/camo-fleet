"""In-memory session registry."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict

from playwright.async_api import Playwright

from .config import WorkerSettings
from .models import SessionDetail, SessionStatus, SessionSummary

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class SessionHandle:
    """Runtime representation of a session."""

    id: str
    worker_id: str
    browser_name: str
    headless: bool
    idle_ttl_seconds: int
    created_at: datetime
    last_seen_at: datetime
    server: Any
    labels: Dict[str, str] = field(default_factory=dict)
    status: SessionStatus = SessionStatus.INIT

    def summary(self) -> SessionSummary:
        return SessionSummary(
            id=self.id,
            status=self.status,
            created_at=self.created_at,
            last_seen_at=self.last_seen_at,
            browser=self.browser_name,
            headless=self.headless,
            idle_ttl_seconds=self.idle_ttl_seconds,
            labels=self.labels,
            worker_id=self.worker_id,
        )

    def detail(self, vnc_info: dict[str, Any]) -> SessionDetail:
        return SessionDetail(
            **self.summary().model_dump(),
            ws_endpoint=self.server.ws_endpoint,
            vnc=vnc_info,
        )


class SessionManager:
    """Manages lifecycle of Playwright sessions."""

    def __init__(self, settings: WorkerSettings, playwright: Playwright) -> None:
        self._settings = settings
        self._playwright = playwright
        self._sessions: dict[str, SessionHandle] = {}
        self._lock = asyncio.Lock()
        self._worker_id = str(uuid.uuid4())
        self._cleanup_task: asyncio.Task[None] | None = None

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def start(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="session-cleaner")

    async def close(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
        await self._close_all()

    async def _close_all(self) -> None:
        async with self._lock:
            handles = list(self._sessions.values())
            self._sessions.clear()
        for handle in handles:
            await self._shutdown_handle(handle)

    async def list(self) -> list[SessionSummary]:
        async with self._lock:
            return [handle.summary() for handle in self._sessions.values()]

    async def list_details(self) -> list[SessionDetail]:
        async with self._lock:
            handles = list(self._sessions.values())
        return [handle.detail(self._build_vnc_payload(handle)) for handle in handles]

    async def get(self, session_id: str) -> SessionHandle | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def create(self, payload: dict[str, Any]) -> SessionHandle:
        browser_name = payload.get("browser") or self._settings.session_defaults.browser
        headless = payload.get("headless")
        if headless is None:
            headless = self._settings.session_defaults.headless
        idle_ttl = payload.get("idle_ttl_seconds") or self._settings.session_defaults.idle_ttl_seconds
        labels = payload.get("labels") or {}

        browser_factory = getattr(self._playwright, browser_name)
        LOGGER.info("Launching %s session headless=%s", browser_name, headless)
        server = await browser_factory.launch_server(headless=headless)
        created_at = datetime.now(tz=timezone.utc)
        handle = SessionHandle(
            id=str(uuid.uuid4()),
            worker_id=self._worker_id,
            browser_name=browser_name,
            headless=headless,
            idle_ttl_seconds=idle_ttl,
            created_at=created_at,
            last_seen_at=created_at,
            server=server,
            labels=labels,
            status=SessionStatus.READY,
        )
        async with self._lock:
            self._sessions[handle.id] = handle
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
            handle.last_seen_at = datetime.now(tz=timezone.utc)
            return handle

    async def _cleanup_loop(self) -> None:
        interval = self._settings.cleanup_interval
        while True:
            await asyncio.sleep(interval)
            await self._cleanup_expired()

    async def _cleanup_expired(self) -> None:
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
            await self._shutdown_handle(handle)

    async def _shutdown_handle(self, handle: SessionHandle) -> None:
        try:
            await handle.server.close()
        finally:
            handle.status = SessionStatus.DEAD

    async def iter_details(self) -> AsyncIterator[SessionDetail]:
        async with self._lock:
            handles = list(self._sessions.values())
        for handle in handles:
            yield handle.detail(self._build_vnc_payload(handle))

    def vnc_payload_for(self, handle: SessionHandle) -> dict[str, Any]:
        return self._build_vnc_payload(handle)

    def _build_vnc_payload(self, handle: SessionHandle) -> dict[str, Any]:
        base_ws = self._settings.vnc_ws_base
        base_http = self._settings.vnc_http_base
        suffix = f"{handle.worker_id}/{handle.id}"
        return {
            "ws": f"{base_ws.rstrip('/')}/{suffix}" if base_ws else None,
            "http": f"{base_http.rstrip('/')}/{suffix}" if base_http else None,
            "password_protected": False,
        }


__all__ = ["SessionManager", "SessionHandle"]

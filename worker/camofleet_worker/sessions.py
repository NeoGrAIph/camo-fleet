"""In-memory session registry."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import tempfile
import time
import uuid
from asyncio import subprocess as aio_subprocess
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict

from playwright.async_api import Playwright

from .config import WorkerSettings
from .models import SessionDetail, SessionStatus, SessionSummary

LOGGER = logging.getLogger(__name__)

BROWSER_SERVER_LAUNCH_TIMEOUT = 30


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

    def detail(self, vnc_info: dict[str, Any], ws_endpoint: str) -> SessionDetail:
        return SessionDetail(
            **self.summary().model_dump(),
            ws_endpoint=ws_endpoint,
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
        return [
            handle.detail(
                self._build_vnc_payload(handle),
                self.ws_endpoint_for(handle),
            )
            for handle in handles
        ]

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

        # Validate requested browser exists before launching the server via the CLI.
        getattr(self._playwright, browser_name)
        LOGGER.info("Launching %s session headless=%s", browser_name, headless)
        server = await self._launch_browser_server(browser_name, headless=headless)
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
            yield handle.detail(
                self._build_vnc_payload(handle),
                self.ws_endpoint_for(handle),
            )

    def vnc_payload_for(self, handle: SessionHandle) -> dict[str, Any]:
        return self._build_vnc_payload(handle)

    def ws_endpoint_for(self, handle: SessionHandle) -> str:
        base = self._settings.ws_endpoint_base
        path = f"/sessions/{handle.id}/ws"
        if not base:
            return path
        return f"{base.rstrip('/')}{path}"

    def _build_vnc_payload(self, handle: SessionHandle) -> dict[str, Any]:
        base_ws = self._settings.vnc_ws_base
        base_http = self._settings.vnc_http_base
        if base_ws:
            ws_url = f"{base_ws.rstrip('/')}/websockify"
        else:
            ws_url = None
        if base_http:
            http_url = f"{base_http.rstrip('/')}/vnc.html?path=websockify"
        else:
            http_url = None
        return {
            "ws": ws_url,
            "http": http_url,
            "password_protected": False,
        }

    async def _launch_browser_server(self, browser_name: str, *, headless: bool) -> "_SubprocessBrowserServer":
        try:
            from playwright._impl._driver import compute_driver_executable
        except ImportError as exc:  # pragma: no cover - defensive, depends on Playwright internals
            raise RuntimeError("Playwright driver executable could not be located") from exc

        config_path: str | None = None
        config = {"headless": headless}
        config_path = await asyncio.to_thread(_write_launch_config, config)
        node_path, cli_path = compute_driver_executable()

        process = await aio_subprocess.create_subprocess_exec(
            node_path,
            cli_path,
            "launch-server",
            f"--browser={browser_name}",
            f"--config={config_path}",
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
        )

        try:
            try:
                raw_endpoint = await asyncio.wait_for(process.stdout.readline(), timeout=BROWSER_SERVER_LAUNCH_TIMEOUT)
            except asyncio.TimeoutError as exc:
                await _terminate_process(process)
                raise RuntimeError(f"Timed out launching Playwright {browser_name} server") from exc

            if not raw_endpoint:
                stderr_output = await process.stderr.read()
                return_code = await process.wait()
                message = stderr_output.decode().strip() or "unknown error"
                raise RuntimeError(
                    f"Failed to launch Playwright {browser_name} server (code {return_code}): {message}"
                )

            ws_endpoint = raw_endpoint.decode().strip()
            stdout_task = asyncio.create_task(
                _drain_stream(process.stdout, f"{browser_name}-stdout"),
                name=f"{browser_name}-server-stdout",
            )
            stderr_task = asyncio.create_task(
                _drain_stream(process.stderr, f"{browser_name}-stderr"),
                name=f"{browser_name}-server-stderr",
            )
            return _SubprocessBrowserServer(process, ws_endpoint, [stdout_task, stderr_task])
        except Exception:
            await _terminate_process(process, kill=True)
            raise
        finally:
            if config_path:
                await asyncio.to_thread(_remove_file, config_path)


class _SubprocessBrowserServer:
    def __init__(
        self,
        process: aio_subprocess.Process,
        ws_endpoint: str,
        drain_tasks: list[asyncio.Task[None]],
    ) -> None:
        self._process = process
        self.ws_endpoint = ws_endpoint
        self._drain_tasks = drain_tasks

    async def close(self) -> None:
        if self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

        for task in self._drain_tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def _drain_stream(stream: asyncio.StreamReader | None, prefix: str) -> None:
    if stream is None:  # pragma: no cover - defensive
        return
    while True:
        line = await stream.readline()
        if not line:
            break
        LOGGER.debug("%s: %s", prefix, line.decode().rstrip())


def _write_launch_config(options: Dict[str, Any]) -> str:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
        json.dump(options, fh)
        fh.write("\n")
        return fh.name


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:  # pragma: no cover - best effort cleanup
        return


async def _terminate_process(process: aio_subprocess.Process, *, kill: bool = False) -> None:
    if process.returncode is not None:
        return
    if not kill:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            LOGGER.warning("Browser server did not exit after terminate; killing")
    process.kill()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


__all__ = ["SessionManager", "SessionHandle"]

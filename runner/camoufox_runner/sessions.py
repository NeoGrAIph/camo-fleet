"""Session management for the Camoufox runner."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import tempfile
import time
import uuid
from asyncio import subprocess as aio_subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from camoufox import launch_options
from playwright._impl._driver import compute_driver_executable
from playwright.async_api import Playwright

from .config import RunnerSettings
from .models import SessionDetail, SessionStatus, SessionSummary

LOGGER = logging.getLogger(__name__)

BROWSER_SERVER_LAUNCH_TIMEOUT = 45


@dataclass(slots=True)
class SessionHandle:
    id: str
    headless: bool
    idle_ttl_seconds: int
    created_at: datetime
    last_seen_at: datetime
    server: "_SubprocessBrowserServer"
    vnc: bool
    start_url: str | None = None
    labels: dict[str, str] = field(default_factory=dict)
    status: SessionStatus = SessionStatus.INIT
    controller_browser: Any | None = None
    controller_context: Any | None = None
    controller_page: Any | None = None

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
        )

    def detail(self, ws_endpoint: str, vnc_payload: dict[str, Any]) -> SessionDetail:
        return SessionDetail(
            **self.summary().model_dump(),
            ws_endpoint=ws_endpoint,
            vnc_info=vnc_payload,
        )


class SessionManager:
    def __init__(self, settings: RunnerSettings, playwright: Playwright) -> None:
        self._settings = settings
        self._playwright = playwright
        self._sessions: dict[str, SessionHandle] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._cleanup_task = asyncio.create_task(self._cleanup_loop(), name="camoufox-cleanup")

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
        if vnc_enabled:
            headless = False
        idle_ttl = payload.get("idle_ttl_seconds") or defaults.idle_ttl_seconds
        labels = payload.get("labels") or {}
        start_url = payload.get("start_url") or defaults.start_url

        server = await self._launch_browser_server(headless=headless, vnc=vnc_enabled)
        created_at = datetime.now(tz=timezone.utc)
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
        )
        await self._bootstrap_session(handle)
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
        while True:
            await asyncio.sleep(self._settings.cleanup_interval)
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
        await self._teardown_controller(handle)
        try:
            await handle.server.close()
        finally:
            handle.status = SessionStatus.DEAD

    async def _bootstrap_session(self, handle: SessionHandle) -> None:
        if not handle.start_url:
            return
        try:
            browser = await self._playwright.firefox.connect(handle.server.ws_endpoint)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(handle.start_url, wait_until="load")
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
            self._build_vnc_payload(handle),
        )

    def _build_vnc_payload(self, handle: SessionHandle) -> dict[str, Any]:
        if not handle.vnc:
            return {"ws": None, "http": None, "password_protected": False}
        base_ws = self._settings.vnc_ws_base
        base_http = self._settings.vnc_http_base
        ws_url = None
        http_url = None
        if base_ws:
            ws_url = f"{base_ws.rstrip('/')}/websockify"
        if base_http:
            http_url = f"{base_http.rstrip('/')}/vnc.html?path=websockify"
        return {"ws": ws_url, "http": http_url, "password_protected": False}

    async def _launch_browser_server(self, *, headless: bool, vnc: bool) -> "_SubprocessBrowserServer":
        opts = launch_options(headless=headless)
        env_vars = {k: v for k, v in (opts.get("env") or {}).items() if v is not None}
        config: dict[str, Any] = {
            "headless": headless,
            "args": opts.get("args") or [],
            "env": env_vars,
        }
        if executable_path := opts.get("executable_path"):
            config["executablePath"] = executable_path
        if prefs := opts.get("firefox_user_prefs"):
            config["firefoxUserPrefs"] = prefs
        if proxy := opts.get("proxy"):
            config["proxy"] = proxy
        if opts.get("ignore_default_args") is not None:
            config["ignoreDefaultArgs"] = opts["ignore_default_args"]
        node_path, cli_path = compute_driver_executable()

        config_path = await asyncio.to_thread(_write_launch_config, config)
        process = await aio_subprocess.create_subprocess_exec(
            node_path,
            cli_path,
            "launch-server",
            "--browser=firefox",
            f"--config={config_path}",
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
        )

        try:
            try:
                raw_endpoint = await asyncio.wait_for(
                    process.stdout.readline(), timeout=BROWSER_SERVER_LAUNCH_TIMEOUT
                )
            except asyncio.TimeoutError as exc:
                await _terminate_process(process)
                raise RuntimeError("Timed out launching Camoufox server") from exc

            if not raw_endpoint:
                stderr_output = await process.stderr.read()
                return_code = await process.wait()
                message = stderr_output.decode().strip() or "unknown error"
                raise RuntimeError(
                    f"Failed to launch Camoufox server (code {return_code}): {message}"
                )

            ws_endpoint = raw_endpoint.decode().strip()
            stdout_task = asyncio.create_task(
                _drain_stream(process.stdout, "camoufox-stdout"),
                name="camoufox-server-stdout",
            )
            stderr_task = asyncio.create_task(
                _drain_stream(process.stderr, "camoufox-stderr"),
                name="camoufox-server-stderr",
            )
            return _SubprocessBrowserServer(process, ws_endpoint, [stdout_task, stderr_task])
        except Exception:
            await _terminate_process(process, kill=True)
            raise
        finally:
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
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            break
        LOGGER.debug("%s: %s", prefix, line.decode().rstrip())


def _write_launch_config(options: dict[str, Any]) -> str:
    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as fh:
        json.dump(options, fh)
        fh.write("\n")
        return fh.name


def _remove_file(path: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        import os

        os.remove(path)


async def _terminate_process(process: aio_subprocess.Process, *, kill: bool = False) -> None:
    if process.returncode is not None:
        return
    if not kill:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
            return
        except asyncio.TimeoutError:
            LOGGER.warning("Camoufox server did not exit after terminate; killing")
    process.kill()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


__all__ = ["SessionManager", "SessionHandle"]

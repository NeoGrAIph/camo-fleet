"""Management of virtual display (VNC) subprocess chains."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from asyncio import subprocess as aio_subprocess
from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlparse, urlunparse

from .config import RunnerSettings

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class VncSlot:
    """Tuple representing one reserved display slot and networking ports."""

    display: int
    vnc_port: int
    ws_port: int


@dataclass(slots=True)
class VncSession:
    """Runtime information for a launched VNC toolchain."""

    slot: VncSlot
    display: str
    http_url: str | None
    ws_url: str | None
    processes: list[aio_subprocess.Process]
    drain_tasks: list[asyncio.Task[None]] = field(default_factory=list)


class VNCUnavailableError(RuntimeError):
    """Raised when VNC-specific operations are requested but tooling is absent."""


class VncResourcePool:
    """Track and allocate VNC display/port tuples across concurrent sessions."""

    def __init__(
        self, *, displays: Iterable[int], vnc_ports: Iterable[int], ws_ports: Iterable[int]
    ) -> None:
        self._display_pool = deque(displays)
        self._vnc_ports = deque(vnc_ports)
        self._ws_ports = deque(ws_ports)
        self._active: set[VncSlot] = set()
        self._lock = asyncio.Lock()

    async def acquire(self) -> VncSlot:
        async with self._lock:
            if not self._display_pool or not self._vnc_ports or not self._ws_ports:
                raise RuntimeError("No available VNC slots")
            slot = VncSlot(
                display=self._display_pool.popleft(),
                vnc_port=self._vnc_ports.popleft(),
                ws_port=self._ws_ports.popleft(),
            )
            self._active.add(slot)
            return slot

    async def release(self, slot: VncSlot | None) -> None:
        if slot is None:
            return
        async with self._lock:
            if slot not in self._active:
                return
            self._active.remove(slot)
            self._display_pool.append(slot.display)
            self._vnc_ports.append(slot.vnc_port)
            self._ws_ports.append(slot.ws_port)


class VncProcessManager:
    """Start and stop VNC-related subprocesses for sessions."""

    def __init__(self, settings: RunnerSettings, *, logger: logging.Logger | None = None) -> None:
        self._settings = settings
        self._logger = logger or LOGGER
        self._pool = VncResourcePool(
            displays=range(settings.vnc_display_min, settings.vnc_display_max + 1),
            vnc_ports=range(settings.vnc_port_min, settings.vnc_port_max + 1),
            ws_ports=range(settings.vnc_ws_port_min, settings.vnc_ws_port_max + 1),
        )
        self._available = all(shutil.which(cmd) for cmd in ("Xvfb", "x11vnc", "websockify"))
        if not self._available:
            self._logger.info("VNC tooling not available; disabling VNC support")

    @property
    def available(self) -> bool:
        return self._available

    async def start_session(self) -> VncSession:
        if not self._available:
            raise VNCUnavailableError("VNC is not supported on this runner")

        slot = await self._pool.acquire()
        display_name = f":{slot.display}"
        processes: list[aio_subprocess.Process] = []
        drain_tasks: list[asyncio.Task[None]] = []
        assets_path = self._settings.vnc_web_assets_path
        try:
            self._logger.debug(
                "Allocating VNC slot display=%s vnc_port=%s ws_port=%s",
                display_name,
                slot.vnc_port,
                slot.ws_port,
            )
            xvfb_proc, xvfb_tasks = await self._spawn_process(
                [
                    "Xvfb",
                    display_name,
                    "-screen",
                    "0",
                    self._settings.vnc_resolution,
                    "+extension",
                    "RANDR",
                    "-nolisten",
                    "tcp",
                ],
                name=f"vnc-xvfb:{slot.display}",
            )
            processes.append(xvfb_proc)
            drain_tasks.extend(xvfb_tasks)
            await self._wait_for_display_socket(slot, xvfb_proc)

            x11vnc_cmd = [
                "x11vnc",
                "-display",
                display_name,
                "-shared",
                "-forever",
                "-rfbport",
                str(slot.vnc_port),
                "-localhost",
                "-nopw",
                "-quiet",
            ]
            x11vnc_proc, x11vnc_tasks = await self._spawn_process(
                x11vnc_cmd,
                name=f"vnc-x11vnc:{slot.display}",
            )
            processes.append(x11vnc_proc)
            drain_tasks.extend(x11vnc_tasks)

            websockify_cmd: list[str] = ["websockify"]
            if assets_path and os.path.isdir(assets_path):
                websockify_cmd.append(f"--web={assets_path}")
            websockify_cmd.extend([
                str(slot.ws_port),
                f"127.0.0.1:{slot.vnc_port}",
            ])
            websockify_proc, websockify_tasks = await self._spawn_process(
                websockify_cmd,
                name=f"vnc-websockify:{slot.ws_port}",
            )
            processes.append(websockify_proc)
            drain_tasks.extend(websockify_tasks)
            await self._wait_for_port("127.0.0.1", slot.ws_port, websockify_proc)

            http_url = self._compose_public_url(
                self._settings.vnc_http_base,
                slot.ws_port,
                "/vnc.html",
                query_params={"path": "websockify"},
            )
            ws_url = self._compose_public_url(
                self._settings.vnc_ws_base,
                slot.ws_port,
                "/websockify",
            )

            return VncSession(
                slot=slot,
                display=display_name,
                http_url=http_url,
                ws_url=ws_url,
                processes=processes,
                drain_tasks=drain_tasks,
            )
        except Exception:
            await self._terminate_vnc_processes(processes, drain_tasks)
            await self._pool.release(slot)
            raise

    async def stop_session(self, session: VncSession | None) -> None:
        if not session:
            return
        try:
            await self._terminate_vnc_processes(session.processes, session.drain_tasks)
        finally:
            await self._pool.release(session.slot)

    async def _terminate_vnc_processes(
        self,
        processes: list[aio_subprocess.Process],
        drain_tasks: list[asyncio.Task[None]],
    ) -> None:
        for process in reversed(processes):
            with contextlib.suppress(Exception):
                await _terminate_process(process, kill=True)
        for task in drain_tasks:
            task.cancel()
        for task in drain_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        processes.clear()
        drain_tasks.clear()

    def _compose_public_url(
        self,
        base: str | None,
        port: int,
        path_suffix: str,
        *,
        query_params: dict[str, str] | None = None,
    ) -> str | None:
        if not base:
            return None
        try:
            parsed = urlparse(base)
        except ValueError:
            self._logger.warning("Invalid VNC base URL: %s", base)
            return None
        scheme = parsed.scheme or ("https" if path_suffix.endswith(".html") else "ws")
        hostname = parsed.hostname or parsed.netloc
        if not hostname:
            self._logger.warning("Unable to determine hostname for VNC base URL: %s", base)
            return None
        userinfo = ""
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo += f":{parsed.password}"
            userinfo += "@"
        if ":" in hostname and not hostname.startswith("["):
            host_part = f"[{hostname}]"
        else:
            host_part = hostname
        netloc = f"{userinfo}{host_part}:{port}"
        base_path = parsed.path.rstrip("/")
        combined_path = f"{base_path}{path_suffix}" if path_suffix else base_path or "/"
        if not combined_path.startswith("/"):
            combined_path = f"/{combined_path}"
        query = urlencode(query_params) if query_params else ""
        return urlunparse((scheme, netloc, combined_path, "", query, ""))

    async def _wait_for_display_socket(
        self, slot: VncSlot, process: aio_subprocess.Process
    ) -> None:
        socket_path = f"/tmp/.X11-unix/X{slot.display}"
        deadline = asyncio.get_running_loop().time() + self._settings.vnc_startup_timeout_seconds
        while True:
            if os.path.exists(socket_path):
                return
            if process.returncode is not None:
                raise RuntimeError(f"Xvfb exited with code {process.returncode}")
            if asyncio.get_running_loop().time() >= deadline:
                raise RuntimeError(f"Timed out waiting for Xvfb display {slot.display}")
            await asyncio.sleep(0.05)

    async def _wait_for_port(
        self,
        host: str,
        port: int,
        process: aio_subprocess.Process,
    ) -> None:
        deadline = asyncio.get_running_loop().time() + self._settings.vnc_startup_timeout_seconds
        while True:
            try:
                reader, writer = await asyncio.open_connection(host, port)
            except OSError as exc:
                if process.returncode is not None:
                    raise RuntimeError(f"websockify exited with code {process.returncode}") from exc
                if asyncio.get_running_loop().time() >= deadline:
                    raise RuntimeError(
                        f"Timed out waiting for websockify on {host}:{port}"
                    ) from None
                await asyncio.sleep(0.1)
                continue
            else:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                return

    async def _spawn_process(
        self,
        args: list[str],
        *,
        name: str,
        env: dict[str, str] | None = None,
    ) -> tuple[aio_subprocess.Process, list[asyncio.Task[None]]]:
        self._logger.debug("Starting %s with args: %s", name, args)
        process = await aio_subprocess.create_subprocess_exec(
            *args,
            stdout=aio_subprocess.PIPE,
            stderr=aio_subprocess.PIPE,
            env=env,
        )
        tasks: list[asyncio.Task[None]] = []
        if process.stdout is not None:
            tasks.append(
                asyncio.create_task(
                    _drain_stream(process.stdout, f"{name}-stdout", self._logger),
                    name=f"{name}-stdout",
                )
            )
        if process.stderr is not None:
            tasks.append(
                asyncio.create_task(
                    _drain_stream(process.stderr, f"{name}-stderr", self._logger),
                    name=f"{name}-stderr",
                )
            )
        return process, tasks


async def _drain_stream(
    stream: asyncio.StreamReader | None, prefix: str, logger: logging.Logger
) -> None:
    if stream is None:
        return
    while True:
        line = await stream.readline()
        if not line:
            break
        logger.debug("%s: %s", prefix, line.decode().rstrip())


async def _terminate_process(process: aio_subprocess.Process, *, kill: bool = False) -> None:
    if process.returncode is not None:
        return
    if not kill:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
            return
        except TimeoutError:
            LOGGER.warning("Process did not exit after terminate; killing")
    process.kill()
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(process.wait(), timeout=5)


def build_vnc_payload(session: VncSession | None, *, enabled: bool) -> dict[str, Any]:
    """Return serialized VNC connection info for API responses."""

    if not enabled or not session:
        return {"ws": None, "http": None, "password_protected": False}
    return {
        "ws": session.ws_url,
        "http": session.http_url,
        "password_protected": False,
    }


__all__ = [
    "VNCUnavailableError",
    "VncProcessManager",
    "VncResourcePool",
    "VncSession",
    "VncSlot",
    "build_vnc_payload",
]

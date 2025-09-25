"""Application factory for the VNC gateway."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import GatewaySettings, VncTarget, load_settings
from .identifiers import extract_identifier

LOGGER = logging.getLogger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = PACKAGE_ROOT / "templates"
STATIC_DIR = PACKAGE_ROOT / "static"


def _render_template(name: str, replacements: dict[str, str]) -> str:
    template_path = TEMPLATE_DIR / name
    template = template_path.read_text(encoding="utf-8")
    for key, value in replacements.items():
        template = template.replace(f"{{{{{key}}}}}", value)
    return template


class CapacityExceededError(RuntimeError):
    """Raised when the session limit is reached."""


class ShutdownInProgressError(RuntimeError):
    """Raised when the service is draining existing sessions."""


class GatewayState:
    """Mutable state shared across request handlers."""

    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self._active_sessions = 0
        self._lock = asyncio.Lock()
        self._drained = asyncio.Event()
        self._drained.set()
        self._closing = False

    @property
    def active_sessions(self) -> int:
        return self._active_sessions

    @property
    def closing(self) -> bool:
        return self._closing

    async def acquire(self) -> None:
        async with self._lock:
            if self._closing:
                raise ShutdownInProgressError
            if self._active_sessions >= self.settings.max_concurrent_sessions:
                raise CapacityExceededError
            self._active_sessions += 1
            self._drained.clear()

    async def release(self) -> None:
        async with self._lock:
            if self._active_sessions > 0:
                self._active_sessions -= 1
            if self._active_sessions == 0:
                self._drained.set()

    async def begin_shutdown(self) -> None:
        async with self._lock:
            self._closing = True
            drained = self._drained
        timeout = self.settings.shutdown_grace_ms / 1000
        try:
            await asyncio.wait_for(drained.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            LOGGER.warning(
                "Timed out waiting for %d active sessions to drain", self._active_sessions
            )


def _seconds_from_ms(value: int) -> float:
    return value / 1000.0


class UpstreamClosedError(Exception):
    """Raised when the upstream VNC server closes the TCP stream."""


class IdleTimeoutError(Exception):
    """Raised when no activity is observed for the configured idle timeout."""


async def _proxy_websocket(
    websocket: WebSocket,
    target: VncTarget,
    settings: GatewaySettings,
) -> None:
    connect_timeout = _seconds_from_ms(settings.tcp_connect_timeout_ms)
    read_timeout = _seconds_from_ms(settings.ws_read_timeout_ms)
    write_timeout = _seconds_from_ms(settings.ws_write_timeout_ms)
    idle_timeout = _seconds_from_ms(settings.tcp_idle_timeout_ms)
    ping_interval = _seconds_from_ms(settings.ws_ping_interval_ms)

    async with asyncio.timeout(connect_timeout):
        reader, writer = await asyncio.open_connection(target.host, target.port)

    subprotocols = websocket.headers.get("sec-websocket-protocol")
    subprotocol = None
    if subprotocols:
        subprotocol = subprotocols.split(",")[0].strip() or None
    await websocket.accept(subprotocol=subprotocol)

    async def close_with_reason(code: int, reason: str) -> None:
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=code, reason=reason)

    async def client_to_tcp() -> None:
        try:
            while True:
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=read_timeout)
                except asyncio.TimeoutError as exc:
                    raise IdleTimeoutError from exc
                message_type = message.get("type")
                if message_type == "websocket.disconnect":
                    break
                if message_type != "websocket.receive":
                    continue
                if "ping" in message:
                    await websocket._send(  # type: ignore[attr-defined]
                        {"type": "websocket.pong", "bytes": message.get("ping") or b""}
                    )
                    continue
                if "pong" in message:
                    continue
                data = message.get("bytes")
                if data is None:
                    text_data = message.get("text")
                    if text_data is None:
                        continue
                    data = text_data.encode("utf-8")
                writer.write(data)
                try:
                    await asyncio.wait_for(writer.drain(), timeout=write_timeout)
                except asyncio.TimeoutError as exc:
                    raise IdleTimeoutError from exc
                activity_event.set()
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def tcp_to_client() -> None:
        try:
            while True:
                try:
                    data = await asyncio.wait_for(reader.read(32_768), timeout=read_timeout)
                except asyncio.TimeoutError as exc:
                    raise IdleTimeoutError from exc
                if not data:
                    raise UpstreamClosedError
                try:
                    await asyncio.wait_for(websocket.send_bytes(data), timeout=write_timeout)
                except asyncio.TimeoutError as exc:
                    raise IdleTimeoutError from exc
                activity_event.set()
        finally:
            await close_with_reason(status.WS_1011_INTERNAL_ERROR, "upstream_closed")

    async def ping_loop() -> None:
        try:
            while True:
                await asyncio.sleep(ping_interval)
                await websocket._send(  # type: ignore[attr-defined]
                    {"type": "websocket.ping", "bytes": b""}
                )
        except asyncio.CancelledError:  # pragma: no cover - shutdown path
            raise
        except Exception:
            # Sending a ping may fail if the socket is already closed.
            return

    async def idle_watchdog() -> None:
        try:
            while True:
                await asyncio.sleep(min(ping_interval, idle_timeout))
                if activity_event.is_set():
                    activity_event.clear()
                    continue
                raise IdleTimeoutError
        except asyncio.CancelledError:  # pragma: no cover - cancellation path
            raise

    activity_event = asyncio.Event()
    activity_event.set()

    client_task = asyncio.create_task(client_to_tcp(), name="vnc-gateway-client-to-tcp")
    server_task = asyncio.create_task(tcp_to_client(), name="vnc-gateway-tcp-to-client")
    ping_task = asyncio.create_task(ping_loop(), name="vnc-gateway-ping")
    idle_task = asyncio.create_task(idle_watchdog(), name="vnc-gateway-idle")

    try:
        done, pending = await asyncio.wait(
            {client_task, server_task, ping_task, idle_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    except UpstreamClosedError:
        pass
    except IdleTimeoutError:
        await close_with_reason(status.WS_1011_INTERNAL_ERROR, "idle_timeout")
        raise
    finally:
        for task in (client_task, server_task, ping_task, idle_task):
            task.cancel()
        await asyncio.gather(client_task, server_task, ping_task, idle_task, return_exceptions=True)


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    state = GatewayState(cfg)

    app = FastAPI(title="Camofleet VNC Gateway")
    app.state.gateway_state = state

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await state.begin_shutdown()

    def get_identifier(request: Request | WebSocket) -> int:
        identifier = extract_identifier(request)
        if identifier is None:
            raise HTTPException(status_code=400, detail={"error": "missing_id"})
        return identifier

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        identifier = extract_identifier(request)
        if identifier is None:
            html = _render_template(
                "error.html",
                {"ERROR_MESSAGE": "VNC session identifier was not provided."},
            )
            return HTMLResponse(html, status_code=400)
        html = _render_template("index.html", {"VNC_ID": str(identifier)})
        return HTMLResponse(html)

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready() -> JSONResponse:
        if state.closing:
            return JSONResponse({"status": "draining"}, status_code=503)
        return JSONResponse({"status": "ok"})

    @app.get("/websockify")
    async def websockify_status(request: Request) -> JSONResponse:
        identifier = extract_identifier(request)
        if identifier is None:
            return JSONResponse({"error": "missing_id"}, status_code=400)
        try:
            cfg.resolve(identifier)
        except KeyError:
            return JSONResponse({"error": "unknown_id"}, status_code=404)
        return JSONResponse({"status": "ready"})

    @app.websocket("/websockify")
    async def websockify_endpoint(websocket: WebSocket) -> None:
        try:
            identifier = get_identifier(websocket)
        except HTTPException:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="missing_id")
            return

        try:
            target = cfg.resolve(identifier)
        except KeyError:
            await websocket.accept()
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="unknown_id")
            return

        try:
            await state.acquire()
        except ShutdownInProgressError:
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER, reason="shutting_down")
            return
        except CapacityExceededError:
            await websocket.close(code=status.WS_1013_TRY_AGAIN_LATER, reason="session_limit")
            return

        LOGGER.info(
            "Proxying VNC id=%s to %s:%s", identifier, target.host, target.port
        )
        try:
            await _proxy_websocket(websocket, target, cfg)
        except (IdleTimeoutError, asyncio.CancelledError):
            pass
        except (OSError, asyncio.TimeoutError):
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="upstream_unreachable")
        finally:
            await state.release()

    return app


__all__ = ["create_app"]

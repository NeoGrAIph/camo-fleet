"""Application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from .config import WorkerSettings, load_settings
from .models import (
    HealthResponse,
    SessionCreateRequest,
    SessionDeleteResponse,
    SessionDetail,
)
from .sessions import SessionManager

LOGGER = logging.getLogger(__name__)


class AppState:
    """Holds runtime state for the FastAPI app."""

    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.manager: SessionManager | None = None
        self.registry = CollectorRegistry()
        self._playwright_context = None

    async def startup(self) -> None:
        LOGGER.info("Starting Camofleet worker")
        self._playwright_context = await async_playwright().start()
        manager = SessionManager(self.settings, self._playwright_context)
        await manager.start()
        self.manager = manager

    async def shutdown(self) -> None:
        LOGGER.info("Shutting down Camofleet worker")
        if self.manager:
            await self.manager.close()
        if self._playwright_context:
            await self._playwright_context.stop()


def get_settings() -> WorkerSettings:
    return load_settings()


def get_app_state(request: Request) -> AppState:
    state = getattr(request.app.state, "app_state", None)
    if not isinstance(state, AppState):
        raise HTTPException(status_code=500, detail="Worker state is unavailable")
    return state


def get_app_state_from_websocket(websocket: WebSocket) -> AppState:
    state = getattr(websocket.app.state, "app_state", None)
    if not isinstance(state, AppState):
        raise HTTPException(status_code=500, detail="Worker state is unavailable")
    return state


def get_manager(state: AppState = Depends(get_app_state)) -> SessionManager:
    if not state.manager:
        raise HTTPException(status_code=503, detail="Worker is still initialising")
    return state.manager


def create_app(settings: WorkerSettings | None = None) -> FastAPI:
    fastapi_app = FastAPI(title="Camofleet Worker", version="0.1.0")

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    resolved_settings = settings or load_settings()
    state = AppState(resolved_settings)
    fastapi_app.state.app_state = state

    @fastapi_app.on_event("startup")
    async def _startup() -> None:
        await state.startup()

    @fastapi_app.on_event("shutdown")
    async def _shutdown() -> None:
        await state.shutdown()

    @fastapi_app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        checks = {"playwright": "ok" if state.manager else "starting"}
        return HealthResponse(status="ok", version=fastapi_app.version, checks=checks)

    @fastapi_app.get("/sessions", response_model=list[SessionDetail])
    async def list_sessions(manager: SessionManager = Depends(get_manager)) -> list[SessionDetail]:
        return await manager.list_details()

    @fastapi_app.post(
        "/sessions",
        status_code=status.HTTP_201_CREATED,
        response_model=SessionDetail,
    )
    async def create_session(
        request: SessionCreateRequest,
        manager: SessionManager = Depends(get_manager),
    ) -> SessionDetail:
        payload = request.model_dump(exclude_unset=True)
        handle = await manager.create(payload)
        vnc_payload = manager.vnc_payload_for(handle)
        return handle.detail(vnc_payload, manager.ws_endpoint_for(handle))

    @fastapi_app.get("/sessions/{session_id}", response_model=SessionDetail)
    async def get_session(session_id: str, manager: SessionManager = Depends(get_manager)) -> SessionDetail:
        handle = await manager.get(session_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Session not found")
        return handle.detail(
            manager.vnc_payload_for(handle),
            manager.ws_endpoint_for(handle),
        )

    @fastapi_app.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
    async def delete_session(
        session_id: str,
        manager: SessionManager = Depends(get_manager),
    ) -> SessionDeleteResponse:
        handle = await manager.delete(session_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionDeleteResponse(id=handle.id, status=handle.status)

    @fastapi_app.post("/sessions/{session_id}/touch", response_model=SessionDetail)
    async def touch_session(
        session_id: str,
        manager: SessionManager = Depends(get_manager),
    ) -> SessionDetail:
        handle = await manager.touch(session_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Session not found")
        return handle.detail(
            manager.vnc_payload_for(handle),
            manager.ws_endpoint_for(handle),
        )

    @fastapi_app.get(resolved_settings.metrics_endpoint)
    async def metrics() -> Response:
        data = generate_latest(state.registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    @fastapi_app.websocket("/sessions/{session_id}/ws")
    async def session_websocket(
        websocket: WebSocket,
        session_id: str,
    ) -> None:
        state = get_app_state_from_websocket(websocket)
        manager = get_manager(state=state)
        handle = await manager.get(session_id)
        if not handle:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await websocket.accept()
        upstream_endpoint = handle.server.ws_endpoint
        await _bridge_websocket(websocket, upstream_endpoint)

    return fastapi_app


app = create_app()


async def _bridge_websocket(websocket: WebSocket, upstream_endpoint: str) -> None:
    try:
        async with websockets.connect(upstream_endpoint, ping_interval=None) as upstream:
            client_to_upstream = asyncio.create_task(
                _forward_client_to_upstream(websocket, upstream),
                name="playwright-bridge-client->upstream",
            )
            upstream_to_client = asyncio.create_task(
                _forward_upstream_to_client(websocket, upstream),
                name="playwright-bridge-upstream->client",
            )
            done, pending = await asyncio.wait(
                {client_to_upstream, upstream_to_client},
                return_when=asyncio.FIRST_EXCEPTION,
            )
            for task in pending:
                task.cancel()
            for task in done:
                exc = task.exception()
                if exc:
                    raise exc
    except (ConnectionClosedError, ConnectionClosedOK, WebSocketDisconnect):
        with contextlib.suppress(RuntimeError):
            await websocket.close()
    except Exception as exc:  # pragma: no cover - defensive logging path
        LOGGER.warning("WebSocket bridge failure: %s", exc)
        with contextlib.suppress(RuntimeError):
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR)


async def _forward_client_to_upstream(websocket: WebSocket, upstream: websockets.WebSocketClientProtocol) -> None:
    try:
        while True:
            message = await websocket.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                await upstream.close()
                break
            if "text" in message and message["text"] is not None:
                await upstream.send(message["text"])
            elif "bytes" in message and message["bytes"] is not None:
                await upstream.send(message["bytes"])
    except WebSocketDisconnect:
        await upstream.close()


async def _forward_upstream_to_client(websocket: WebSocket, upstream: websockets.WebSocketClientProtocol) -> None:
    try:
        async for data in upstream:
            if isinstance(data, (bytes, bytearray)):
                await websocket.send_bytes(data)
            else:
                await websocket.send_text(data)
    finally:
        with contextlib.suppress(RuntimeError):
            await websocket.close()


__all__ = ["create_app", "app"]

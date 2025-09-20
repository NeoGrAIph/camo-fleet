"""Application factory."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

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


def get_manager(state: AppState = Depends(lambda: app_state)) -> SessionManager:
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

    state = AppState(settings or load_settings())

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
        return handle.detail(vnc_payload)

    @fastapi_app.get("/sessions/{session_id}", response_model=SessionDetail)
    async def get_session(session_id: str, manager: SessionManager = Depends(get_manager)) -> SessionDetail:
        handle = await manager.get(session_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Session not found")
        return handle.detail(manager.vnc_payload_for(handle))

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
        return handle.detail(manager.vnc_payload_for(handle))

    @fastapi_app.get(settings.metrics_endpoint)
    async def metrics() -> Response:
        data = generate_latest(state.registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    return fastapi_app


app_state = AppState(load_settings())
app = create_app(app_state.settings)


__all__ = ["create_app", "app"]

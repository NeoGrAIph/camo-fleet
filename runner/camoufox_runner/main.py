"""Application factory for Camoufox runner."""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest

from shared import __version__

from .config import RunnerSettings, load_settings
from .models import (
    HealthResponse,
    SessionCreateRequest,
    SessionDeleteResponse,
    SessionDetail,
)
from .sessions import SessionManager, VNCUnavailableError

LOGGER = logging.getLogger(__name__)


class AppState:
    def __init__(self, settings: RunnerSettings) -> None:
        self.settings = settings
        self.manager: SessionManager | None = None
        self.registry = CollectorRegistry()
        self._playwright = None

    async def startup(self) -> None:
        LOGGER.info("Starting Camoufox runner")
        self._playwright = await async_playwright().start()
        manager = SessionManager(self.settings, self._playwright)
        await manager.start()
        self.manager = manager

    async def shutdown(self) -> None:
        LOGGER.info("Shutting down Camoufox runner")
        if self.manager:
            await self.manager.close()
        if self._playwright:
            await self._playwright.stop()


def get_settings() -> RunnerSettings:
    return load_settings()


def get_app_state(app: FastAPI) -> AppState:
    """Return the runner application state."""

    state = getattr(app.state, "app_state", None)
    if not isinstance(state, AppState):  # pragma: no cover - defensive branch
        raise RuntimeError("Runner app state is not initialised")
    return state


def create_app(settings: RunnerSettings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    app = FastAPI(title="Camoufox Runner", version=__version__)
    allow_origins = cfg.cors_origins or ["*"]
    allow_all_origins = "*" in allow_origins
    cors_allow_origins = ["*"] if allow_all_origins else allow_origins
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allow_origins,
        allow_credentials=not allow_all_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    state = AppState(cfg)
    app.state.app_state = state

    @app.on_event("startup")
    async def _startup() -> None:
        await get_app_state(app).startup()

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await get_app_state(app).shutdown()

    def get_state(request: Request) -> AppState:
        return get_app_state(request.app)

    def get_manager(state: AppState = Depends(get_state)) -> SessionManager:
        if not state.manager:
            raise HTTPException(status_code=503, detail="Runner initialising")
        return state.manager

    @app.get("/health", response_model=HealthResponse)
    async def health(state: AppState = Depends(get_state)) -> HealthResponse:
        checks = {"playwright": "ok" if state.manager else "starting"}
        return HealthResponse(status="ok", version=app.version, checks=checks)

    @app.get("/sessions", response_model=list[SessionDetail])
    async def list_sessions(manager: SessionManager = Depends(get_manager)) -> list[SessionDetail]:
        return await manager.list_details()

    @app.post("/sessions", response_model=SessionDetail, status_code=status.HTTP_201_CREATED)
    async def create_session(
        request: SessionCreateRequest,
        manager: SessionManager = Depends(get_manager),
    ) -> SessionDetail:
        payload = request.model_dump(exclude_unset=True)
        try:
            handle = await manager.create(payload)
        except VNCUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
            ) from exc
        return manager.detail_for(handle)

    @app.get("/sessions/{session_id}", response_model=SessionDetail)
    async def get_session(
        session_id: str, manager: SessionManager = Depends(get_manager)
    ) -> SessionDetail:
        handle = await manager.get(session_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Session not found")
        return manager.detail_for(handle)

    @app.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
    async def delete_session(
        session_id: str,
        manager: SessionManager = Depends(get_manager),
    ) -> SessionDeleteResponse:
        handle = await manager.delete(session_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Session not found")
        return SessionDeleteResponse(id=handle.id, status=handle.status)

    @app.post("/sessions/{session_id}/touch", response_model=SessionDetail)
    async def touch_session(
        session_id: str,
        manager: SessionManager = Depends(get_manager),
    ) -> SessionDetail:
        handle = await manager.touch(session_id)
        if not handle:
            raise HTTPException(status_code=404, detail="Session not found")
        return handle.detail(manager.ws_endpoint_for(handle), manager._build_vnc_payload(handle))

    @app.get(cfg.metrics_endpoint)
    async def metrics(state: AppState = Depends(get_state)) -> Response:
        data = generate_latest(state.registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()


__all__ = ["create_app", "app"]

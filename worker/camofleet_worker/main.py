"""Worker service that proxies requests to the Camoufox runner sidecar."""

from __future__ import annotations

import logging
import uuid

import httpx
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    WebSocket,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
import websockets

from shared import __version__, bridge_websocket

from .config import WorkerSettings, load_settings
from .models import (
    HealthResponse,
    SessionCreateRequest,
    SessionDeleteResponse,
    SessionDetail,
    SessionStatus,
)
from .runner_client import RunnerClient

LOGGER = logging.getLogger(__name__)


class AppState:
    def __init__(self, settings: WorkerSettings) -> None:
        self.settings = settings
        self.runner = RunnerClient(settings.runner_base_url)
        self.registry = CollectorRegistry()
        self.worker_id = str(uuid.uuid4())

    async def shutdown(self) -> None:
        await self.runner.close()


def get_settings() -> WorkerSettings:
    return load_settings()


def get_app_state(app: FastAPI) -> AppState:
    """Return the application state stored on a FastAPI instance."""

    state = getattr(app.state, "app_state", None)
    if not isinstance(state, AppState):  # pragma: no cover - defensive branch
        raise RuntimeError("Worker app state is not initialised")
    return state


def create_app(settings: WorkerSettings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    app = FastAPI(title="Camofleet Worker", version=__version__)
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

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        await get_app_state(app).shutdown()

    def get_state(request: Request) -> AppState:
        return get_app_state(request.app)

    @app.get("/health", response_model=HealthResponse)
    async def health(app_state: AppState = Depends(get_state)) -> HealthResponse:
        try:
            runner_health = await app_state.runner.health()
            status_text = runner_health.get("status", "unknown")
            checks = runner_health.get("checks", {})
        except Exception as exc:  # pragma: no cover - defensive path
            LOGGER.warning("Runner health check failed: %s", exc)
            status_text = "degraded"
            checks = {"runner": "unreachable"}
        return HealthResponse(status=status_text, version=app.version, checks=checks)

    @app.get("/sessions", response_model=list[SessionDetail])
    async def list_sessions(app_state: AppState = Depends(get_state)) -> list[SessionDetail]:
        data = await app_state.runner.list_sessions()
        return [_to_worker_detail(app_state, item) for item in data]

    @app.post("/sessions", response_model=SessionDetail, status_code=status.HTTP_201_CREATED)
    async def create_session(
        request: SessionCreateRequest,
        app_state: AppState = Depends(get_state),
    ) -> SessionDetail:
        if request.vnc and not app_state.settings.supports_vnc:
            raise HTTPException(status_code=400, detail="VNC is not supported by this worker")
        payload = request.model_dump(exclude_unset=True)
        payload.setdefault("headless", app_state.settings.session_defaults.headless)
        payload.setdefault("idle_ttl_seconds", app_state.settings.session_defaults.idle_ttl_seconds)
        data = await app_state.runner.create_session(payload)
        return _to_worker_detail(app_state, data)

    @app.get("/sessions/{session_id}", response_model=SessionDetail)
    async def get_session(
        session_id: str, app_state: AppState = Depends(get_state)
    ) -> SessionDetail:
        try:
            data = await app_state.runner.get_session(session_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Session not found") from exc
            raise
        return _to_worker_detail(app_state, data)

    @app.delete("/sessions/{session_id}", response_model=SessionDeleteResponse)
    async def delete_session(
        session_id: str, app_state: AppState = Depends(get_state)
    ) -> SessionDeleteResponse:
        try:
            data = await app_state.runner.delete_session(session_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Session not found") from exc
            raise
        return SessionDeleteResponse(id=data["id"], status=SessionStatus(data["status"]))

    @app.post("/sessions/{session_id}/touch", response_model=SessionDetail)
    async def touch_session(
        session_id: str, app_state: AppState = Depends(get_state)
    ) -> SessionDetail:
        try:
            data = await app_state.runner.touch_session(session_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise HTTPException(status_code=404, detail="Session not found") from exc
            raise
        return _to_worker_detail(app_state, data)

    @app.get(cfg.metrics_endpoint)
    async def metrics(app_state: AppState = Depends(get_state)) -> Response:
        data = generate_latest(app_state.registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    @app.websocket("/sessions/{session_id}/ws")
    async def session_websocket(
        session_id: str,
        websocket: WebSocket,
        app_state: AppState = Depends(get_state),
    ) -> None:
        """Proxy WebSocket traffic between the client and the underlying runner session."""

        await websocket.accept()
        try:
            data = await app_state.runner.get_session(session_id)
        except httpx.HTTPStatusError:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        except Exception:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        upstream_endpoint = data.get("ws_endpoint")
        if not upstream_endpoint:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return
        await bridge_websocket(
            websocket,
            lambda: websockets.connect(upstream_endpoint, ping_interval=None),
            logger=LOGGER,
            log_context=f"worker bridge for session {session_id}",
            error_close_code=status.WS_1011_INTERNAL_ERROR,
        )

    return app


def _to_worker_detail(app_state: AppState, data: dict) -> SessionDetail:
    return SessionDetail(
        id=data["id"],
        status=SessionStatus(data["status"]),
        created_at=data["created_at"],
        last_seen_at=data["last_seen_at"],
        browser="camoufox",
        headless=data["headless"],
        idle_ttl_seconds=data["idle_ttl_seconds"],
        labels=data.get("labels", {}),
        worker_id=app_state.worker_id,
        vnc_enabled=data.get("vnc", False),
        start_url_wait=data.get("start_url_wait", "load"),
        ws_endpoint=f"/sessions/{data['id']}/ws",
        vnc=data.get("vnc_info", {}),
    )


__all__ = ["create_app"]

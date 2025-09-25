"""Control-plane FastAPI app."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx
import websockets
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
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from .config import ControlSettings, WorkerConfig, load_settings
from .models import (
    CreateSessionRequest,
    CreateSessionResponse,
    SessionDescriptor,
    WorkerStatus,
)
from .service import aclose_worker_clients, worker_client

def _ensure_shared_importable() -> None:
    """Locate the ``shared`` package when running from an installed wheel."""

    module_path = Path(__file__).resolve()
    candidates: list[Path] = list(module_path.parents)
    pythonpath = os.environ.get("PYTHONPATH", "")
    if pythonpath:
        candidates.extend(Path(entry) for entry in pythonpath.split(os.pathsep) if entry)
    candidates.append(Path.cwd())

    seen: set[str] = set()
    for base in candidates:
        for root in (base, base.parent):
            root_str = str(root)
            if root_str in seen:
                continue
            seen.add(root_str)
            shared_dir = root / "shared"
            if shared_dir.exists():
                if root_str not in sys.path:
                    sys.path.insert(0, root_str)
                return


try:  # pragma: no cover - executed only in developer environments
    from shared import __version__, bridge_websocket
except ModuleNotFoundError:  # pragma: no cover - fallback when ``shared`` isn't installed
    _ensure_shared_importable()
    from shared import __version__, bridge_websocket

LOGGER = logging.getLogger(__name__)


class AppState:
    def __init__(self, settings: ControlSettings) -> None:
        self.settings = settings
        self._rr_index = 0
        self.registry = CollectorRegistry()
        self.proxy_success_total = Counter(
            "control_plane_proxy_success_total",
            "Count of successful proxy requests to workers.",
            ("worker", "operation"),
            registry=self.registry,
        )
        self.proxy_error_total = Counter(
            "control_plane_proxy_error_total",
            "Count of failed proxy requests to workers.",
            ("worker", "operation"),
            registry=self.registry,
        )
        self.proxy_request_duration = Histogram(
            "control_plane_proxy_request_duration_seconds",
            "Time spent proxying HTTP requests to workers.",
            ("worker", "operation"),
            registry=self.registry,
        )
        self.active_websockets = Gauge(
            "control_plane_active_websockets",
            "Number of active WebSocket proxy connections.",
            ("worker",),
            registry=self.registry,
        )

    def list_workers(self) -> list[WorkerConfig]:
        return list(self.settings.workers)

    def pick_worker(
        self, preferred: str | None = None, *, require_vnc: bool = False
    ) -> WorkerConfig:
        workers = [w for w in self.list_workers() if not require_vnc or w.supports_vnc]
        if preferred:
            for worker in workers:
                if worker.name == preferred:
                    return worker
            raise HTTPException(status_code=404, detail="Worker not found")
        if not workers:
            raise HTTPException(status_code=503, detail="No workers configured")
        worker = workers[self._rr_index % len(workers)]
        self._rr_index += 1
        return worker

    async def proxy_request(
        self,
        worker: WorkerConfig,
        operation: str,
        func: Callable[..., Awaitable[httpx.Response]],
        *args,
        **kwargs,
    ) -> httpx.Response:
        """Execute a worker request while recording metrics."""

        labels = {"worker": worker.name, "operation": operation}
        start = perf_counter()
        try:
            response = await func(*args, **kwargs)
        except Exception:
            duration = perf_counter() - start
            self.proxy_error_total.labels(**labels).inc()
            self.proxy_request_duration.labels(**labels).observe(duration)
            raise
        duration = perf_counter() - start
        if response.status_code < 400:
            self.proxy_success_total.labels(**labels).inc()
        else:
            self.proxy_error_total.labels(**labels).inc()
        self.proxy_request_duration.labels(**labels).observe(duration)
        return response


def get_settings() -> ControlSettings:
    return load_settings()


def get_app_state(app: FastAPI) -> AppState:
    """Return the control-plane application state."""

    state = getattr(app.state, "app_state", None)
    if not isinstance(state, AppState):  # pragma: no cover - defensive branch
        raise RuntimeError("Control-plane app state is not initialised")
    return state


def create_app(settings: ControlSettings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    app = FastAPI(title="Camofleet Control", version=__version__)
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

    def get_state(request: Request) -> AppState:
        return get_app_state(request.app)

    @app.on_event("shutdown")
    async def close_worker_clients() -> None:
        await aclose_worker_clients()

    @app.get("/health")
    async def health(state: AppState = Depends(get_state)) -> dict:
        worker_statuses = await gather_worker_status(state)
        healthy = all(item.healthy for item in worker_statuses) if worker_statuses else False
        return {
            "status": "ok" if healthy else "degraded",
            "workers": [s.model_dump() for s in worker_statuses],
        }

    @app.get("/workers", response_model=list[WorkerStatus])
    async def list_workers_endpoint(state: AppState = Depends(get_state)) -> list[WorkerStatus]:
        return await gather_worker_status(state)

    @app.get("/sessions", response_model=list[SessionDescriptor])
    async def list_sessions(state: AppState = Depends(get_state)) -> list[SessionDescriptor]:
        workers = state.list_workers()
        if not workers:
            return []

        semaphore = asyncio.Semaphore(max(1, state.settings.list_sessions_concurrency))

        async def fetch_worker_sessions(worker: WorkerConfig) -> list[SessionDescriptor]:
            async with semaphore:
                async with worker_client(worker, cfg) as client:
                    try:
                        response = await state.proxy_request(
                            worker, "list_sessions", client.list_sessions
                        )
                        response.raise_for_status()
                    except httpx.HTTPError as exc:  # pragma: no cover - network failure
                        LOGGER.warning("Failed to query worker %s: %s", worker.name, exc)
                        return []
            sessions: list[SessionDescriptor] = []
            for item in response.json():
                public_ws_endpoint = build_public_ws_endpoint(cfg, worker.name, item["id"])
                vnc_payload = item.get("vnc", item.get("vnc_info", {})) or {}
                vnc_enabled = item.get("vnc_enabled")
                if vnc_enabled is None and vnc_payload:
                    vnc_enabled = bool(vnc_payload.get("http") or vnc_payload.get("ws"))
                # Apply optional public VNC base overrides if configured on the worker
                if isinstance(vnc_payload, dict):
                    vnc_payload = apply_vnc_overrides(worker, vnc_payload)
                sessions.append(
                    SessionDescriptor(
                        worker=worker.name,
                        id=item["id"],
                        status=item["status"],
                        created_at=item["created_at"],
                        last_seen_at=item["last_seen_at"],
                        browser=item.get("browser", "camoufox"),
                        headless=item["headless"],
                        idle_ttl_seconds=item["idle_ttl_seconds"],
                        labels=item.get("labels", {}),
                        ws_endpoint=public_ws_endpoint,
                        vnc_enabled=vnc_enabled,
                        vnc=vnc_payload,
                        start_url_wait=item.get("start_url_wait"),
                    )
                )
            return sessions

        tasks = [fetch_worker_sessions(worker) for worker in workers]
        results = await asyncio.gather(*tasks)
        return [item for worker_sessions in results for item in worker_sessions]

    @app.post(
        "/sessions", response_model=CreateSessionResponse, status_code=status.HTTP_201_CREATED
    )
    async def create_session(
        request: CreateSessionRequest,
        state: AppState = Depends(get_state),
    ) -> CreateSessionResponse:
        worker = state.pick_worker(request.worker, require_vnc=request.vnc)
        payload = request.model_dump(exclude_unset=True)
        payload.pop("worker", None)
        async with worker_client(worker, cfg) as client:
            response = await state.proxy_request(
                worker, "create_session", client.create_session, payload
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        body = response.json()
        body["ws_endpoint"] = build_public_ws_endpoint(cfg, worker.name, body["id"])
        body.setdefault("browser", "camoufox")
        if "vnc" not in body and "vnc_info" in body:
            body["vnc"] = body.pop("vnc_info")
        if "vnc_enabled" not in body and "vnc" in body:
            body["vnc_enabled"] = bool(body["vnc"].get("http") or body["vnc"].get("ws"))
        vnc_payload = body.get("vnc")
        if isinstance(vnc_payload, dict):
            body["vnc"] = apply_vnc_overrides(worker, vnc_payload)
        return CreateSessionResponse(worker=worker.name, **body)

    @app.get("/sessions/{worker_name}/{session_id}", response_model=SessionDescriptor)
    async def get_session(
        worker_name: str, session_id: str, state: AppState = Depends(get_state)
    ) -> SessionDescriptor:
        worker = state.pick_worker(worker_name)
        async with worker_client(worker, cfg) as client:
            response = await state.proxy_request(
                worker, "get_session", client.get_session, session_id
            )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Session not found")
        response.raise_for_status()
        body = response.json()
        body["ws_endpoint"] = build_public_ws_endpoint(cfg, worker.name, body["id"])
        body.setdefault("browser", "camoufox")
        if "vnc" not in body and "vnc_info" in body:
            body["vnc"] = body.pop("vnc_info")
        if "vnc_enabled" not in body and "vnc" in body:
            body["vnc_enabled"] = bool(body["vnc"].get("http") or body["vnc"].get("ws"))
        vnc_payload = body.get("vnc")
        if isinstance(vnc_payload, dict):
            body["vnc"] = apply_vnc_overrides(worker, vnc_payload)
        return SessionDescriptor(worker=worker.name, **body)

    @app.delete("/sessions/{worker_name}/{session_id}")
    async def delete_session(
        worker_name: str, session_id: str, state: AppState = Depends(get_state)
    ) -> dict:
        worker = state.pick_worker(worker_name)
        async with worker_client(worker, cfg) as client:
            response = await state.proxy_request(
                worker, "delete_session", client.delete_session, session_id
            )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Session not found")
        response.raise_for_status()
        return response.json()

    @app.post("/sessions/{worker_name}/{session_id}/touch", response_model=SessionDescriptor)
    async def touch_session(
        worker_name: str,
        session_id: str,
        state: AppState = Depends(get_state),
    ) -> SessionDescriptor:
        worker = state.pick_worker(worker_name)
        async with worker_client(worker, cfg) as client:
            response = await state.proxy_request(
                worker, "touch_session", client.touch_session, session_id
            )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Session not found")
        response.raise_for_status()
        body = response.json()
        body["ws_endpoint"] = build_public_ws_endpoint(cfg, worker.name, body["id"])
        body.setdefault("browser", "camoufox")
        if "vnc" not in body and "vnc_info" in body:
            body["vnc"] = body.pop("vnc_info")
        if "vnc_enabled" not in body and "vnc" in body:
            body["vnc_enabled"] = bool(body["vnc"].get("http") or body["vnc"].get("ws"))
        vnc_payload = body.get("vnc")
        if isinstance(vnc_payload, dict):
            body["vnc"] = apply_vnc_overrides(worker, vnc_payload)
        return SessionDescriptor(worker=worker.name, **body)

    @app.get(cfg.metrics_endpoint)
    async def metrics(state: AppState = Depends(get_state)) -> Response:
        data = generate_latest(state.registry)
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    @app.websocket("/sessions/{worker_name}/{session_id}/ws")
    async def session_websocket(
        websocket: WebSocket,
        worker_name: str,
        session_id: str,
        state: AppState = Depends(get_state),
    ) -> None:
        """Proxy WebSocket traffic through the control-plane towards a specific worker."""

        worker = state.pick_worker(worker_name)
        upstream_endpoint = build_worker_ws_endpoint(worker, session_id)
        await websocket.accept()
        websocket_labels = {"worker": worker.name}
        state.active_websockets.labels(**websocket_labels).inc()
        try:
            await bridge_websocket(
                websocket,
                lambda: websockets.connect(
                    upstream_endpoint,
                    ping_interval=None,
                    open_timeout=cfg.request_timeout,
                ),
                logger=LOGGER,
                log_context=f"control-plane proxy to worker {worker.name}",
                error_close_code=status.WS_1011_INTERNAL_ERROR,
            )
        finally:
            state.active_websockets.labels(**websocket_labels).dec()

    return app


async def gather_worker_status(state: AppState) -> list[WorkerStatus]:
    worker_list = list(state.list_workers())

    async def _fetch_status(worker: WorkerConfig) -> WorkerStatus:
        async with worker_client(worker, state.settings) as client:
            try:
                response = await state.proxy_request(worker, "health", client.health)
                response.raise_for_status()
                detail = response.json()
                return WorkerStatus(
                    name=worker.name,
                    healthy=True,
                    detail=detail,
                    supports_vnc=worker.supports_vnc,
                )
            except httpx.HTTPError as exc:  # pragma: no cover
                LOGGER.warning("Worker %s unhealthy: %s", worker.name, exc)
                return WorkerStatus(
                    name=worker.name,
                    healthy=False,
                    detail={"error": str(exc)},
                    supports_vnc=worker.supports_vnc,
                )

    if not worker_list:
        return []

    return list(await asyncio.gather(*(_fetch_status(worker) for worker in worker_list)))


def build_public_ws_endpoint(settings: ControlSettings, worker_name: str, session_id: str) -> str:
    prefix = normalise_public_prefix(settings.public_api_prefix)
    return f"{prefix}/sessions/{worker_name}/{session_id}/ws"


def build_worker_ws_endpoint(worker: WorkerConfig, session_id: str) -> str:
    parsed = urlparse(worker.url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if path:
        path = f"{path}/sessions/{session_id}/ws"
    else:
        path = f"/sessions/{session_id}/ws"
    base = parsed._replace(scheme=scheme, path=path, params="", query="", fragment="")
    return urlunparse(base)


def _default_port_for_scheme(scheme: str | None) -> int | None:
    if not scheme:
        return None
    scheme = scheme.lower()
    if scheme in {"http", "ws"}:
        return 80
    if scheme in {"https", "wss"}:
        return 443
    return None


def _format_hostname(hostname: str | None) -> str:
    if not hostname:
        return ""
    if ":" in hostname and not hostname.startswith("["):
        return f"[{hostname}]"
    return hostname


def _build_netloc(
    username: str | None, password: str | None, hostname: str | None, port: int | None
) -> str:
    host = _format_hostname(hostname)
    if not host:
        return ""
    userinfo = ""
    if username:
        userinfo = username
        if password is not None:
            userinfo += f":{password}"
        userinfo += "@"
    if port is not None:
        return f"{userinfo}{host}:{port}"
    return f"{userinfo}{host}"


_VNC_ID_PATTERN = re.compile(r"/vnc/(?P<id>\d+)(?:/|$)")


def _extract_vnc_identifier(parsed: Any) -> int | None:
    query = parse_qs(parsed.query)
    for key in ("token", "id"):
        values = query.get(key)
        if values:
            candidate = values[0]
            if candidate.isdigit():
                return int(candidate)
    match = _VNC_ID_PATTERN.search(parsed.path or "")
    if match:
        return int(match.group("id"))
    return None


def apply_vnc_overrides(worker: WorkerConfig, payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return payload

    mutated = dict(payload)
    overrides = (("ws", worker.vnc_ws), ("http", worker.vnc_http))
    for key, override_template in overrides:
        original_url = payload.get(key)
        if not original_url or not override_template:
            continue

        try:
            parsed_original = urlparse(original_url)
        except ValueError:
            continue

        try:
            parsed_override_template = urlparse(override_template)
        except ValueError:
            continue

        port_placeholder_used = "{port}" in override_template
        id_placeholder_used = "{id}" in override_template
        host_placeholder_used = "{host}" in override_template

        session_port = parsed_original.port
        if session_port is None:
            session_port = _default_port_for_scheme(parsed_original.scheme)

        identifier = _extract_vnc_identifier(parsed_original)
        effective_port = identifier if identifier is not None else session_port

        if port_placeholder_used and effective_port is None:
            continue
        if host_placeholder_used and not parsed_original.hostname:
            continue
        if id_placeholder_used and identifier is None:
            continue

        rendered_override = override_template
        if port_placeholder_used and effective_port is not None:
            rendered_override = rendered_override.replace("{port}", str(effective_port))
        if id_placeholder_used and identifier is not None:
            rendered_override = rendered_override.replace("{id}", str(identifier))
        if host_placeholder_used and parsed_original.hostname:
            rendered_override = rendered_override.replace("{host}", parsed_original.hostname)

        try:
            parsed_override = urlparse(rendered_override)
        except ValueError:
            continue

        if not parsed_override.scheme or not parsed_override.hostname:
            continue

        final_scheme = parsed_override.scheme

        netloc_template = parsed_override_template.netloc
        hostport_template = netloc_template.rsplit("@", 1)[-1]
        port_placeholder_in_netloc = port_placeholder_used and ":{port}" in hostport_template

        if parsed_override.port is not None:
            final_port = parsed_override.port
        elif port_placeholder_in_netloc:
            final_port = session_port
        elif port_placeholder_used:
            final_port = session_port
        else:
            final_port = None

        final_username = (
            parsed_override.username
            if parsed_override.username is not None
            else parsed_original.username
        )
        final_password = (
            parsed_override.password
            if parsed_override.username is not None
            else parsed_original.password
        )
        final_hostname = parsed_override.hostname or parsed_original.hostname
        if not final_hostname:
            continue

        final_path = parsed_override.path or parsed_original.path
        final_params = parsed_override.params or parsed_original.params
        final_query = parsed_override.query or parsed_original.query
        final_fragment = parsed_override.fragment or parsed_original.fragment

        netloc = _build_netloc(final_username, final_password, final_hostname, final_port)
        if not netloc:
            continue

        mutated[key] = urlunparse(
            (
                final_scheme,
                netloc,
                final_path,
                final_params,
                final_query,
                final_fragment,
            )
        )

    return mutated


def normalise_public_prefix(prefix: str) -> str:
    value = (prefix or "").strip()
    if not value or value == "/":
        return ""
    if not value.startswith("/"):
        value = f"/{value}"
    return value.rstrip("/")


__all__ = ["create_app"]

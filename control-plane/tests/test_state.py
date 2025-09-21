from __future__ import annotations

from typing import Any

import pytest
from camofleet_control.config import ControlSettings, WorkerConfig
from camofleet_control.main import (
    AppState,
    build_public_ws_endpoint,
    build_worker_ws_endpoint,
    create_app,
    normalise_public_prefix,
)
from fastapi import HTTPException
from fastapi.middleware.cors import CORSMiddleware


def make_settings(workers: list[WorkerConfig]) -> ControlSettings:
    return ControlSettings(workers=workers)


def _get_cors_options(app: Any) -> dict[str, Any]:
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return middleware.kwargs
    raise AssertionError("CORS middleware not configured")


def test_pick_worker_round_robin() -> None:
    workers = [
        WorkerConfig(name="a", url="http://a"),
        WorkerConfig(name="b", url="http://b"),
    ]
    state = AppState(make_settings(workers))
    assert state.pick_worker().name == "a"
    assert state.pick_worker().name == "b"
    assert state.pick_worker().name == "a"


def test_create_app_initialises_state() -> None:
    app = create_app(ControlSettings())
    assert isinstance(app.state.app_state, AppState)


def test_pick_worker_by_name() -> None:
    workers = [WorkerConfig(name="x", url="http://x")]
    state = AppState(make_settings(workers))
    assert state.pick_worker("x").name == "x"
    with pytest.raises(HTTPException):
        state.pick_worker("missing")


def test_pick_worker_requires_vnc() -> None:
    workers = [
        WorkerConfig(name="headless", url="http://a", supports_vnc=False),
        WorkerConfig(name="vnc", url="http://b", supports_vnc=True),
    ]
    state = AppState(make_settings(workers))
    assert state.pick_worker(require_vnc=True).name == "vnc"
    with pytest.raises(HTTPException):
        state.pick_worker("headless", require_vnc=True)


def test_normalise_public_prefix() -> None:
    assert normalise_public_prefix("/") == ""
    assert normalise_public_prefix("/api/") == "/api"
    assert normalise_public_prefix("api") == "/api"
    assert normalise_public_prefix("") == ""


def test_build_public_ws_endpoint() -> None:
    settings = ControlSettings(public_api_prefix="/api")
    assert (
        build_public_ws_endpoint(settings, "worker-1", "session-1")
        == "/api/sessions/worker-1/session-1/ws"
    )
    settings = ControlSettings(public_api_prefix="/")
    assert (
        build_public_ws_endpoint(settings, "worker-2", "session-2")
        == "/sessions/worker-2/session-2/ws"
    )


def test_build_worker_ws_endpoint() -> None:
    worker = WorkerConfig(name="a", url="http://worker:8080")
    assert build_worker_ws_endpoint(worker, "sess") == "ws://worker:8080/sessions/sess/ws"
    worker = WorkerConfig(name="b", url="https://worker.example")
    assert build_worker_ws_endpoint(worker, "sess") == "wss://worker.example/sessions/sess/ws"
    worker = WorkerConfig(name="c", url="https://worker.example/prefix")
    assert (
        build_worker_ws_endpoint(worker, "sess") == "wss://worker.example/prefix/sessions/sess/ws"
    )


def test_control_plane_cors_for_specific_origins() -> None:
    settings = ControlSettings(cors_origins=["https://ui.example"])
    app = create_app(settings)
    options = _get_cors_options(app)
    assert options["allow_origins"] == ["https://ui.example"]
    assert options["allow_credentials"] is True


def test_control_plane_cors_allows_any_origin_without_credentials() -> None:
    settings = ControlSettings(cors_origins=["*"])
    app = create_app(settings)
    options = _get_cors_options(app)
    assert options["allow_origins"] == ["*"]
    assert options["allow_credentials"] is False

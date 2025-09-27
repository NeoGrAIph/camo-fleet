from __future__ import annotations

import pytest

from camofleet_control.config import ControlSettings, WorkerConfig
from urllib.parse import parse_qs, urlparse

from camofleet_control.main import (
    AppState,
    build_public_vnc_payload,
    build_public_ws_endpoint,
    build_worker_ws_endpoint,
    normalise_public_prefix,
)
from fastapi import HTTPException


def make_settings(workers: list[WorkerConfig]) -> ControlSettings:
    return ControlSettings(workers=workers)


def test_pick_worker_round_robin() -> None:
    workers = [
        WorkerConfig(name="a", url="http://a"),
        WorkerConfig(name="b", url="http://b"),
    ]
    state = AppState(make_settings(workers))
    assert state.pick_worker().name == "a"
    assert state.pick_worker().name == "b"
    assert state.pick_worker().name == "a"


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
    assert (
        build_worker_ws_endpoint(worker, "sess")
        == "ws://worker:8080/sessions/sess/ws"
    )
    worker = WorkerConfig(name="b", url="https://worker.example")
    assert (
        build_worker_ws_endpoint(worker, "sess")
        == "wss://worker.example/sessions/sess/ws"
    )
    worker = WorkerConfig(name="c", url="https://worker.example/prefix")
    assert (
        build_worker_ws_endpoint(worker, "sess")
        == "wss://worker.example/prefix/sessions/sess/ws"
    )


def test_build_public_vnc_payload_without_overrides() -> None:
    worker = WorkerConfig(name="vnc", url="http://worker:8080", supports_vnc=True)
    payload = {
        "http": "http://localhost:6930/vnc.html?path=websockify",
        "ws": "ws://localhost:6930/websockify",
        "password_protected": False,
    }

    result = build_public_vnc_payload(worker, "session-1", payload)

    assert result == payload
    assert result is not payload


def test_build_public_vnc_payload_with_overrides() -> None:
    worker = WorkerConfig(
        name="vnc",
        url="http://worker:8080",
        supports_vnc=True,
        vnc_http="https://public.example/vnc/{id}",
        vnc_ws="wss://public.example/websockify?token={id}",
    )
    payload = {
        "http": "http://localhost:6930/vnc.html?path=websockify",
        "ws": "ws://localhost:6930/websockify",
    }

    result = build_public_vnc_payload(worker, "session-42", payload)

    parsed_http = urlparse(result["http"])
    assert parsed_http.scheme == "https"
    assert parsed_http.netloc == "public.example"
    assert parsed_http.path == "/vnc/session-42/vnc.html"
    http_query = parse_qs(parsed_http.query)
    assert http_query["path"] == ["vnc/session-42/websockify"]
    assert http_query["target_port"] == ["6930"]

    parsed_ws = urlparse(result["ws"])
    assert parsed_ws.scheme == "wss"
    assert parsed_ws.netloc == "public.example"
    assert parsed_ws.path == "/websockify"
    ws_query = parse_qs(parsed_ws.query)
    assert ws_query["token"] == ["session-42"]
    assert ws_query["target_port"] == ["6930"]

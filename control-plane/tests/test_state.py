from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

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


def test_list_sessions_queries_workers_concurrently(monkeypatch: pytest.MonkeyPatch) -> None:
    workers = [
        WorkerConfig(name="worker-a", url="http://worker-a"),
        WorkerConfig(name="worker-b", url="http://worker-b"),
    ]
    settings = ControlSettings(workers=workers, public_api_prefix="/")
    state = AppState(settings)

    start_events = {worker.name: asyncio.Event() for worker in workers}
    proceed_event = asyncio.Event()

    class DummyResponse:
        def __init__(self, payload: list[dict]) -> None:
            self._payload = payload
            self.status_code = 200

        def json(self) -> list[dict]:
            return self._payload

        def raise_for_status(self) -> None:
            return None

    @asynccontextmanager
    async def fake_worker_client(worker: WorkerConfig, cfg: ControlSettings):
        class DummyClient:
            async def list_sessions(self) -> DummyResponse:
                start_events[worker.name].set()
                await proceed_event.wait()
                return DummyResponse(
                    [
                        {
                            "id": f"{worker.name}-session",
                            "status": "running",
                            "created_at": "2024-01-01T00:00:00Z",
                            "last_seen_at": "2024-01-01T00:00:00Z",
                            "headless": False,
                            "idle_ttl_seconds": 30,
                            "labels": {"worker": worker.name},
                            "browser": "camoufox",
                            "vnc": {},
                            "start_url_wait": None,
                        }
                    ]
                )

            async def close(self) -> None:
                return None

        yield DummyClient()

    monkeypatch.setattr("camofleet_control.main.worker_client", fake_worker_client)

    async def exercise() -> None:
        app = create_app(settings)
        list_sessions_route = next(
            route
            for route in app.routes
            if getattr(route, "path", None) == "/sessions" and "GET" in getattr(route, "methods", set())
        )
        list_sessions_endpoint = list_sessions_route.endpoint

        list_task = asyncio.create_task(list_sessions_endpoint(state=state))

        await asyncio.wait_for(
            asyncio.gather(*(event.wait() for event in start_events.values())),
            timeout=0.5,
        )
        proceed_event.set()
        sessions = await list_task

        assert sorted((session.worker, session.id) for session in sessions) == [
            ("worker-a", "worker-a-session"),
            ("worker-b", "worker-b-session"),
        ]

    asyncio.run(exercise())

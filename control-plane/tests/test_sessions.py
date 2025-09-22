from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import pytest
from camofleet_control.config import ControlSettings, WorkerConfig
from camofleet_control.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeResponse:
    def __init__(self, payload: Any):
        self._payload = payload
        self.status_code = 200

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:  # pragma: no cover - matching httpx API
        return None


class FakeClient:
    def __init__(self, worker_name: str, payload: list[dict], delay: float) -> None:
        self._worker_name = worker_name
        self._payload = payload
        self._delay = delay

    async def list_sessions(self) -> FakeResponse:
        await asyncio.sleep(self._delay)
        return FakeResponse(self._payload)


@pytest.mark.anyio("asyncio")
async def test_list_sessions_merges_results_independent_of_completion_order(monkeypatch):
    workers = [
        WorkerConfig(name="slow", url="http://slow"),
        WorkerConfig(name="fast", url="http://fast"),
    ]
    settings = ControlSettings(workers=workers, list_sessions_concurrency=2)
    app = create_app(settings)

    responses = {
        "slow": [
            {
                "id": "a",
                "status": "running",
                "created_at": "2024-01-01T00:00:00Z",
                "last_seen_at": "2024-01-01T00:00:10Z",
                "headless": False,
                "idle_ttl_seconds": 60,
                "labels": {"group": "blue"},
            }
        ],
        "fast": [
            {
                "id": "b",
                "status": "starting",
                "created_at": "2024-01-01T00:01:00Z",
                "last_seen_at": "2024-01-01T00:01:05Z",
                "headless": True,
                "idle_ttl_seconds": 120,
                "labels": {"group": "red"},
            }
        ],
    }

    delays = {"slow": 0.1, "fast": 0.0}

    @asynccontextmanager
    async def fake_worker_client(worker: WorkerConfig, _settings: ControlSettings):
        yield FakeClient(worker.name, responses[worker.name], delays[worker.name])

    monkeypatch.setattr("camofleet_control.main.worker_client", fake_worker_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.get("/sessions")

    assert resp.status_code == 200
    payload = resp.json()
    assert {item["worker"] for item in payload} == {"slow", "fast"}
    assert {item["id"] for item in payload} == {"a", "b"}


@pytest.mark.anyio("asyncio")
async def test_touch_session_returns_full_descriptor(monkeypatch) -> None:
    worker = WorkerConfig(name="alpha", url="http://alpha", supports_vnc=True)
    settings = ControlSettings(workers=[worker])
    app = create_app(settings)

    payload = {
        "id": "sess-1",
        "status": "READY",
        "created_at": "2024-01-01T00:00:00Z",
        "last_seen_at": "2024-01-01T00:05:00Z",
        "browser": "camoufox",
        "headless": False,
        "idle_ttl_seconds": 300,
        "labels": {"team": "qa"},
        "worker_id": "worker-alpha",
        "vnc_enabled": True,
        "ws_endpoint": "/sessions/sess-1/ws",
        "vnc": {
            "ws": "ws://alpha/vnc",
            "http": "http://alpha/vnc",
            "password_protected": False,
        },
        "start_url_wait": "load",
    }

    class TouchClient:
        def __init__(self, data: dict[str, Any]) -> None:
            self._data = data

        async def touch_session(self, session_id: str) -> FakeResponse:
            assert session_id == self._data["id"]
            return FakeResponse(self._data)

    @asynccontextmanager
    async def fake_worker_client(worker_config: WorkerConfig, _settings: ControlSettings):
        assert worker_config.name == worker.name
        yield TouchClient(payload)

    monkeypatch.setattr("camofleet_control.main.worker_client", fake_worker_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        resp = await client.post(f"/sessions/{worker.name}/{payload['id']}/touch")

    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == payload["id"]
    assert body["worker"] == worker.name
    assert body["ws_endpoint"] == f"/sessions/{worker.name}/{payload['id']}/ws"
    assert body["vnc"] == payload["vnc"]
    assert body["vnc_enabled"] is True
    assert body["browser"] == payload["browser"]
    assert body["last_seen_at"] == payload["last_seen_at"]

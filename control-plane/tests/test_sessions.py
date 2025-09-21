from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient

from camofleet_control.config import ControlSettings, WorkerConfig
from camofleet_control.main import create_app


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeResponse:
    def __init__(self, payload: list[dict]):
        self._payload = payload
        self.status_code = 200

    def json(self) -> list[dict]:
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

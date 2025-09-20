"""HTTP client facade to worker APIs."""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx

from .config import ControlSettings, WorkerConfig


class WorkerClient:
    """A reusable async HTTP client that targets a worker."""

    def __init__(self, worker: WorkerConfig, settings: ControlSettings) -> None:
        self.worker = worker
        self._settings = settings
        self._client = httpx.AsyncClient(base_url=worker.url, timeout=settings.request_timeout)

    async def health(self) -> httpx.Response:
        return await self._client.get("/health")

    async def list_sessions(self) -> httpx.Response:
        return await self._client.get("/sessions")

    async def get_session(self, session_id: str) -> httpx.Response:
        return await self._client.get(f"/sessions/{session_id}")

    async def delete_session(self, session_id: str) -> httpx.Response:
        return await self._client.delete(f"/sessions/{session_id}")

    async def create_session(self, payload: dict) -> httpx.Response:
        return await self._client.post("/sessions", json=payload)

    async def touch_session(self, session_id: str) -> httpx.Response:
        return await self._client.post(f"/sessions/{session_id}/touch")

    async def close(self) -> None:
        await self._client.aclose()


@asynccontextmanager
async def worker_client(worker: WorkerConfig, settings: ControlSettings):
    client = WorkerClient(worker, settings)
    try:
        yield client
    finally:
        await client.close()


__all__ = ["WorkerClient", "worker_client"]

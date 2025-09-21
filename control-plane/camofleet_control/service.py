"""HTTP client facade to worker APIs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx

from .config import ControlSettings, WorkerConfig

_CLIENTS: dict[tuple[str, str], httpx.AsyncClient] = {}
_LOCK: asyncio.Lock | None = None


def _worker_key(worker: WorkerConfig) -> tuple[str, str]:
    """Return a stable key for identifying worker clients."""

    return (worker.name, worker.url)


async def _get_lock() -> asyncio.Lock:
    """Return a module level lock, creating it on demand."""

    global _LOCK
    if _LOCK is None:
        _LOCK = asyncio.Lock()
    return _LOCK


async def _get_or_create_http_client(
    worker: WorkerConfig, settings: ControlSettings
) -> httpx.AsyncClient:
    """Return a cached :class:`httpx.AsyncClient` for the worker."""

    key = _worker_key(worker)
    lock = await _get_lock()
    async with lock:
        client = _CLIENTS.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                base_url=worker.url,
                timeout=settings.request_timeout,
            )
            _CLIENTS[key] = client
        return client


class WorkerClient:
    """A thin wrapper around a shared async HTTP client for a worker.

    The underlying :class:`httpx.AsyncClient` is created once per
    :class:`WorkerConfig` and cached until the application shuts down.
    See :func:`aclose_worker_clients` for the lifecycle management hook.
    """

    def __init__(self, worker: WorkerConfig, http_client: httpx.AsyncClient) -> None:
        self.worker = worker
        self._client = http_client

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Expose the underlying HTTP client for introspection in tests."""

        return self._client

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


@asynccontextmanager
async def worker_client(
    worker: WorkerConfig, settings: ControlSettings
) -> AsyncIterator[WorkerClient]:
    """Yield a :class:`WorkerClient` backed by a shared HTTP connection."""

    client = WorkerClient(worker, await _get_or_create_http_client(worker, settings))
    yield client


async def aclose_worker_clients() -> None:
    """Close all cached worker HTTP clients.

    This should be invoked during the application's shutdown sequence to ensure
    that all shared :class:`httpx.AsyncClient` instances are properly closed.
    """

    lock = await _get_lock()
    async with lock:
        clients = list(_CLIENTS.values())
        _CLIENTS.clear()
    await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)


__all__ = ["WorkerClient", "worker_client", "aclose_worker_clients"]

"""HTTP client wrapper for the Camoufox runner sidecar."""

from __future__ import annotations

import httpx


class RunnerClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def health(self) -> dict:
        response = await self._client.get("/health")
        response.raise_for_status()
        return response.json()

    async def list_sessions(self) -> list[dict]:
        response = await self._client.get("/sessions")
        response.raise_for_status()
        return response.json()

    async def create_session(self, payload: dict) -> dict:
        response = await self._client.post("/sessions", json=payload)
        response.raise_for_status()
        return response.json()

    async def get_session(self, session_id: str) -> dict:
        response = await self._client.get(f"/sessions/{session_id}")
        response.raise_for_status()
        return response.json()

    async def delete_session(self, session_id: str) -> dict:
        response = await self._client.delete(f"/sessions/{session_id}")
        response.raise_for_status()
        return response.json()

    async def touch_session(self, session_id: str) -> dict:
        response = await self._client.post(f"/sessions/{session_id}/touch")
        response.raise_for_status()
        return response.json()

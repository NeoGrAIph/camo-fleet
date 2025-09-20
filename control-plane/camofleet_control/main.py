"""Control-plane FastAPI app."""

from __future__ import annotations

import logging
from typing import Iterable

import httpx
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from .config import ControlSettings, WorkerConfig, load_settings
from .models import (
    CreateSessionRequest,
    CreateSessionResponse,
    SessionDescriptor,
    WorkerStatus,
)
from .service import worker_client

LOGGER = logging.getLogger(__name__)


class AppState:
    def __init__(self, settings: ControlSettings) -> None:
        self.settings = settings
        self._rr_index = 0

    def list_workers(self) -> list[WorkerConfig]:
        return list(self.settings.workers)

    def pick_worker(self, preferred: str | None = None) -> WorkerConfig:
        workers = self.list_workers()
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


def get_settings() -> ControlSettings:
    return load_settings()


def create_app(settings: ControlSettings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    app = FastAPI(title="Camofleet Control", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    state = AppState(cfg)

    def get_state() -> AppState:
        return state

    @app.get("/health")
    async def health(state: AppState = Depends(get_state)) -> dict:
        worker_statuses = await gather_worker_status(state.list_workers(), cfg)
        healthy = all(item.healthy for item in worker_statuses) if worker_statuses else False
        return {"status": "ok" if healthy else "degraded", "workers": [s.model_dump() for s in worker_statuses]}

    @app.get("/workers", response_model=list[WorkerStatus])
    async def list_workers_endpoint(state: AppState = Depends(get_state)) -> list[WorkerStatus]:
        return await gather_worker_status(state.list_workers(), cfg)

    @app.get("/sessions", response_model=list[SessionDescriptor])
    async def list_sessions(state: AppState = Depends(get_state)) -> list[SessionDescriptor]:
        results: list[SessionDescriptor] = []
        for worker in state.list_workers():
            async with worker_client(worker, cfg) as client:
                try:
                    response = await client.list_sessions()
                    response.raise_for_status()
                except httpx.HTTPError as exc:  # pragma: no cover - network failure
                    LOGGER.warning("Failed to query worker %s: %s", worker.name, exc)
                    continue
                for item in response.json():
                    results.append(
                        SessionDescriptor(
                            worker=worker.name,
                            id=item["id"],
                            status=item["status"],
                            created_at=item["created_at"],
                            last_seen_at=item["last_seen_at"],
                            browser=item["browser"],
                            headless=item["headless"],
                            idle_ttl_seconds=item["idle_ttl_seconds"],
                            labels=item.get("labels", {}),
                            ws_endpoint=item["ws_endpoint"],
                            vnc=item.get("vnc", {}),
                        )
                    )
        return results

    @app.post("/sessions", response_model=CreateSessionResponse, status_code=status.HTTP_201_CREATED)
    async def create_session(
        request: CreateSessionRequest,
        state: AppState = Depends(get_state),
    ) -> CreateSessionResponse:
        worker = state.pick_worker(request.worker)
        payload = request.model_dump(exclude_unset=True)
        payload.pop("worker", None)
        async with worker_client(worker, cfg) as client:
            response = await client.create_session(payload)
        if response.status_code >= 400:
            raise HTTPException(status_code=response.status_code, detail=response.text)
        body = response.json()
        return CreateSessionResponse(worker=worker.name, **body)

    @app.get("/sessions/{worker_name}/{session_id}", response_model=SessionDescriptor)
    async def get_session(worker_name: str, session_id: str, state: AppState = Depends(get_state)) -> SessionDescriptor:
        worker = state.pick_worker(worker_name)
        async with worker_client(worker, cfg) as client:
            response = await client.get_session(session_id)
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Session not found")
        response.raise_for_status()
        body = response.json()
        return SessionDescriptor(worker=worker.name, **body)

    @app.delete("/sessions/{worker_name}/{session_id}")
    async def delete_session(worker_name: str, session_id: str, state: AppState = Depends(get_state)) -> dict:
        worker = state.pick_worker(worker_name)
        async with worker_client(worker, cfg) as client:
            response = await client.delete_session(session_id)
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Session not found")
        response.raise_for_status()
        return response.json()

    return app


async def gather_worker_status(workers: Iterable[WorkerConfig], cfg: ControlSettings) -> list[WorkerStatus]:
    statuses: list[WorkerStatus] = []
    for worker in workers:
        async with worker_client(worker, cfg) as client:
            try:
                response = await client.health()
                response.raise_for_status()
                detail = response.json()
                statuses.append(WorkerStatus(name=worker.name, healthy=True, detail=detail))
            except httpx.HTTPError as exc:  # pragma: no cover
                LOGGER.warning("Worker %s unhealthy: %s", worker.name, exc)
                statuses.append(WorkerStatus(name=worker.name, healthy=False, detail={"error": str(exc)}))
    return statuses


__all__ = ["create_app"]

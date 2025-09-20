"""Response models shared across endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WorkerStatus(BaseModel):
    name: str
    healthy: bool
    detail: dict[str, Any]


class SessionDescriptor(BaseModel):
    worker: str
    id: str
    status: str
    created_at: datetime
    last_seen_at: datetime
    browser: str
    headless: bool
    idle_ttl_seconds: int
    labels: dict[str, str]
    ws_endpoint: str
    vnc: dict[str, Any]


class CreateSessionRequest(BaseModel):
    worker: str | None = None
    browser: str | None = None
    headless: bool | None = None
    idle_ttl_seconds: int | None = None
    labels: dict[str, str] | None = None


class CreateSessionResponse(SessionDescriptor):
    pass


__all__ = [
    "WorkerStatus",
    "SessionDescriptor",
    "CreateSessionRequest",
    "CreateSessionResponse",
]

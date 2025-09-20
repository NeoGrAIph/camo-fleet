"""Response models shared across endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class WorkerStatus(BaseModel):
    name: str
    healthy: bool
    detail: dict[str, Any]
    supports_vnc: bool


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
    vnc_enabled: bool | None = None
    vnc: dict[str, Any]


class CreateSessionRequest(BaseModel):
    worker: str | None = None
    headless: bool | None = None
    idle_ttl_seconds: int | None = None
    labels: dict[str, str] | None = None
    start_url: str | None = None
    vnc: bool = False


class CreateSessionResponse(SessionDescriptor):
    pass


__all__ = [
    "WorkerStatus",
    "SessionDescriptor",
    "CreateSessionRequest",
    "CreateSessionResponse",
]

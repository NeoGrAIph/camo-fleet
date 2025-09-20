"""Pydantic models exposed over the public API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    """Lifecycle states for a session."""

    INIT = "INIT"
    READY = "READY"
    TERMINATING = "TERMINATING"
    DEAD = "DEAD"


class SessionCreateRequest(BaseModel):
    """Inbound payload for creating a new browser session."""

    browser: Annotated[str | None, Field(pattern=r"^(chromium|firefox|webkit)$")] = None
    headless: bool | None = None
    idle_ttl_seconds: Annotated[int | None, Field(ge=30, le=3600)] = None
    start_url: Annotated[str | None, Field(max_length=1024)] = None
    labels: dict[str, str] | None = None


class SessionSummary(BaseModel):
    """Short session description."""

    id: str
    status: SessionStatus
    created_at: datetime
    last_seen_at: datetime
    browser: str
    headless: bool
    idle_ttl_seconds: int
    labels: dict[str, str]
    worker_id: str


class SessionDetail(SessionSummary):
    """Extended session representation."""

    ws_endpoint: str
    vnc: dict[str, str | bool | None]


class SessionDeleteResponse(BaseModel):
    """Response returned after scheduling a deletion."""

    id: str
    status: SessionStatus


class HealthResponse(BaseModel):
    """Simple health payload."""

    status: str
    version: str
    checks: dict[str, str]


__all__ = [
    "SessionStatus",
    "SessionCreateRequest",
    "SessionSummary",
    "SessionDetail",
    "SessionDeleteResponse",
    "HealthResponse",
]

"""Pydantic models exposed by the runner API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    INIT = "INIT"
    READY = "READY"
    TERMINATING = "TERMINATING"
    DEAD = "DEAD"


class SessionCreateRequest(BaseModel):
    headless: bool | None = None
    idle_ttl_seconds: Annotated[int | None, Field(ge=30, le=3600)] = None
    start_url: Annotated[str | None, Field(max_length=1024)] = None
    start_url_wait: Literal["none", "domcontentloaded", "load"] | None = None
    labels: dict[str, str] | None = None
    vnc: bool = False


class SessionSummary(BaseModel):
    id: str
    status: SessionStatus
    created_at: datetime
    last_seen_at: datetime
    headless: bool
    idle_ttl_seconds: int
    labels: dict[str, str]
    vnc: bool
    start_url_wait: Literal["none", "domcontentloaded", "load"]


class SessionDetail(SessionSummary):
    ws_endpoint: str
    vnc_info: dict[str, str | bool | None]


class SessionDeleteResponse(BaseModel):
    id: str
    status: SessionStatus


class HealthResponse(BaseModel):
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

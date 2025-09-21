"""Pydantic models exposed over the public API."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field


class SessionStatus(str, Enum):
    """Lifecycle states for a session."""

    INIT = "INIT"
    READY = "READY"
    TERMINATING = "TERMINATING"
    DEAD = "DEAD"


class SessionCreateRequest(BaseModel):
    """Inbound payload forwarded from the control-plane to the runner.

    Поле :attr:`vnc` сигнализирует runner'у, что требуется VNC toolchain;
    worker дополнительно проверяет поддержку VNC у своего инстанса.
    """

    headless: bool | None = None
    idle_ttl_seconds: Annotated[int | None, Field(ge=30, le=3600)] = None
    start_url: Annotated[str | None, Field(max_length=1024)] = None
    start_url_wait: Literal["none", "domcontentloaded", "load"] | None = None
    labels: dict[str, str] | None = None
    vnc: bool = False
    # Optional per-session proxy override (Playwright format)
    proxy: dict[str, str] | None = None


class SessionSummary(BaseModel):
    """Short session description exposed by the worker API.

    :attr:`vnc_enabled` — булево отображение runner-флага `vnc` и/или наличия
    подключений в :attr:`SessionDetail.vnc`.
    """

    id: str
    status: SessionStatus
    created_at: datetime
    last_seen_at: datetime
    browser: str
    headless: bool
    idle_ttl_seconds: int
    labels: dict[str, str]
    worker_id: str
    vnc_enabled: bool
    start_url_wait: Literal["none", "domcontentloaded", "load"]


class SessionDetail(SessionSummary):
    """Extended session representation.

    Поле :attr:`vnc` наследует структуру runner'а (`ws`, `http`,
    `password_protected`) и напрямую передаётся control-plane/UI.
    """

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

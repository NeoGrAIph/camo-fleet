"""Response models shared across endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class WorkerStatus(BaseModel):
    """Health summary for a worker returned by the control-plane."""

    name: str
    healthy: bool
    detail: dict[str, Any]
    supports_vnc: bool


class SessionDescriptor(BaseModel):
    """Session payload exposed by the control-plane/UI layer.

    Поле :attr:`vnc_enabled` наследует значение из worker'а, а :attr:`vnc`
    содержит словарь, полученный от runner'а (`ws`, `http`,
    `password_protected`).
    """

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
    start_url_wait: Literal["none", "domcontentloaded", "load"] | None = None


class CreateSessionRequest(BaseModel):
    worker: str | None = None
    headless: bool | None = None
    idle_ttl_seconds: int | None = None
    labels: dict[str, str] | None = None
    start_url: str | None = None
    vnc: bool = False
    start_url_wait: Literal["none", "domcontentloaded", "load"] | None = None
    # Optional per-session proxy override (Playwright-compatible)
    proxy: dict[str, str] | None = None


class CreateSessionResponse(SessionDescriptor):
    pass


__all__ = [
    "WorkerStatus",
    "SessionDescriptor",
    "CreateSessionRequest",
    "CreateSessionResponse",
]

"""Pydantic models used by the control-plane API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class WorkerStatus(BaseModel):
    """Snapshot of a worker's health response."""

    name: str
    healthy: bool
    detail: dict[str, Any]
    supports_vnc: bool


class SessionDescriptor(BaseModel):
    """Aggregate information about a session across the fleet."""

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
    """Incoming payload when users request a new session."""

    worker: str | None = None
    headless: bool | None = None
    idle_ttl_seconds: int | None = None
    labels: dict[str, str] | None = None
    start_url: str | None = None
    vnc: bool = False
    start_url_wait: Literal["none", "domcontentloaded", "load"] | None = None


class CreateSessionResponse(SessionDescriptor):
    """Session representation returned by POST /sessions."""


class DiagnosticsProbe(BaseModel):
    """Status of a single protocol probe for a target URL."""

    protocol: str
    status: str
    detail: str


class DiagnosticsTarget(BaseModel):
    """Aggregated probe results for a particular URL."""

    url: str
    probes: list[DiagnosticsProbe]


class WorkerDiagnostics(BaseModel):
    """Diagnostics summary captured for a single worker."""

    name: str
    healthy: bool
    diagnostics_status: str
    checks: dict[str, str]
    targets: list[DiagnosticsTarget]
    notes: list[str]


class DiagnosticsReport(BaseModel):
    """Environment diagnostics snapshot returned to the UI."""

    generated_at: datetime
    workers: list[WorkerDiagnostics]


__all__ = [
    "WorkerStatus",
    "SessionDescriptor",
    "CreateSessionRequest",
    "CreateSessionResponse",
    "DiagnosticsProbe",
    "DiagnosticsTarget",
    "WorkerDiagnostics",
    "DiagnosticsReport",
]

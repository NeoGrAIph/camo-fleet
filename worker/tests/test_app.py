from __future__ import annotations

import pytest
from types import SimpleNamespace
from typing import cast

from fastapi import HTTPException
from starlette.requests import Request

from camofleet_worker.config import WorkerSettings
from camofleet_worker.main import AppState, get_app_state, get_manager
from camofleet_worker.sessions import SessionManager


async def _receive() -> dict:
    return {"type": "http.request"}


def _make_scope(app) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
        "scheme": "http",
        "http_version": "1.1",
        "app": app,
    }


def test_get_app_state_returns_runtime_state() -> None:
    settings = WorkerSettings()
    app_state = AppState(settings)

    app = SimpleNamespace(state=SimpleNamespace(app_state=app_state))

    request = Request(_make_scope(app), _receive)
    resolved = get_app_state(request)
    assert resolved is app_state


def test_get_app_state_without_state_raises() -> None:
    app = SimpleNamespace(state=SimpleNamespace())

    request = Request(_make_scope(app), _receive)
    with pytest.raises(HTTPException) as exc:
        get_app_state(request)
    assert exc.value.status_code == 500


def test_get_manager_requires_initialised_state() -> None:
    settings = WorkerSettings()
    app_state = AppState(settings)

    with pytest.raises(HTTPException) as exc:
        get_manager(state=app_state)
    assert exc.value.status_code == 503


def test_get_manager_returns_active_manager() -> None:
    settings = WorkerSettings()
    app_state = AppState(settings)
    sentinel = object()
    app_state.manager = cast(SessionManager, sentinel)

    assert get_manager(state=app_state) is sentinel

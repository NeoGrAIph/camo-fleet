from __future__ import annotations

from typing import Any

from camoufox_runner.config import RunnerSettings
from camoufox_runner.main import create_app
from fastapi.middleware.cors import CORSMiddleware


def _get_cors_options(app: Any) -> dict[str, Any]:
    for middleware in app.user_middleware:
        if middleware.cls is CORSMiddleware:
            return middleware.kwargs
    raise AssertionError("CORS middleware not configured")


def test_runner_cors_for_specific_origins() -> None:
    settings = RunnerSettings(cors_origins=["https://worker.example"])
    app = create_app(settings)
    options = _get_cors_options(app)
    assert options["allow_origins"] == ["https://worker.example"]
    assert options["allow_credentials"] is True


def test_runner_cors_allows_any_origin_without_credentials() -> None:
    settings = RunnerSettings(cors_origins=["*"])
    app = create_app(settings)
    options = _get_cors_options(app)
    assert options["allow_origins"] == ["*"]
    assert options["allow_credentials"] is False

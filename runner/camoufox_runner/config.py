"""Configuration helpers for the Camoufox runner service."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SessionDefaults(BaseModel):
    """Default session parameters."""

    idle_ttl_seconds: Annotated[int, Field(ge=30, le=3600)] = 300
    headless: bool = False
    start_url: str | None = None


class RunnerSettings(BaseSettings):
    """Runtime settings for the runner."""

    model_config = SettingsConfigDict(env_prefix="RUNNER_", env_file=".env")

    host: str = "0.0.0.0"
    port: int = 8070
    metrics_endpoint: str = "/metrics"
    cleanup_interval: Annotated[int, Field(gt=0, le=3600)] = 15
    session_defaults: SessionDefaults = Field(default_factory=SessionDefaults)
    vnc_ws_base: str | None = None
    vnc_http_base: str | None = None


@lru_cache
def load_settings() -> RunnerSettings:
    """Return cached settings instance."""

    return RunnerSettings()


__all__ = ["RunnerSettings", "load_settings", "SessionDefaults"]

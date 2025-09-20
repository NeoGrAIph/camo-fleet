"""Configuration helpers for the worker service."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SessionDefaults(BaseModel):
    """Default session parameters loaded from configuration."""

    idle_ttl_seconds: Annotated[int, Field(ge=30, le=3600)] = 300
    headless: bool = False


class WorkerSettings(BaseSettings):
    """Runtime settings for the worker service."""

    model_config = SettingsConfigDict(env_prefix="WORKER_", env_file=".env")

    host: str = "0.0.0.0"
    port: int = 8080
    shutdown_timeout: int = 10

    metrics_endpoint: str = "/metrics"

    session_defaults: SessionDefaults = Field(default_factory=SessionDefaults)
    cleanup_interval: Annotated[int, Field(gt=0, le=3600)] = 15

    runner_base_url: str = "http://127.0.0.1:8070"
    supports_vnc: bool = False


@lru_cache
def load_settings() -> WorkerSettings:
    """Return cached settings instance."""

    return WorkerSettings()


__all__ = ["WorkerSettings", "load_settings"]

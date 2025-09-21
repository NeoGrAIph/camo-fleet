"""Settings for the control-plane service."""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkerConfig(BaseModel):
    """Describe a worker entry."""

    name: Annotated[str, Field(min_length=1)]
    url: Annotated[str, Field(min_length=1)]
    vnc_ws: str | None = None
    vnc_http: str | None = None
    supports_vnc: bool = False


class ControlSettings(BaseSettings):
    """Runtime configuration."""

    model_config = SettingsConfigDict(env_prefix="CONTROL_", env_file=".env")

    host: str = "0.0.0.0"
    port: int = 9000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    workers: list[WorkerConfig] = Field(
        default_factory=lambda: [
            WorkerConfig(
                name="local",
                url="http://worker:8080",
                supports_vnc=False,
            ),
        ]
    )
    request_timeout: float = 10.0
    public_api_prefix: str = "/"
    metrics_endpoint: str = "/metrics"


@lru_cache
def load_settings() -> ControlSettings:
    return ControlSettings()


__all__ = ["ControlSettings", "WorkerConfig", "load_settings"]

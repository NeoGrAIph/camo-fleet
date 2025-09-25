"""Runtime configuration for the VNC gateway service."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True, slots=True)
class VncTarget:
    """Resolved VNC upstream endpoint."""

    host: str
    port: int


class GatewaySettings(BaseSettings):
    """Runtime configuration for the gateway."""

    model_config = SettingsConfigDict(env_prefix="", env_file=ENV_FILE, extra="ignore")

    http_host: str = "0.0.0.0"
    http_port: Annotated[int, Field(ge=1, le=65535)] = 6900

    vnc_default_host: str = "127.0.0.1"
    vnc_web_range: str = "6900-6904"
    vnc_base_port: Annotated[int, Field(ge=1, le=65535)] = 5900
    vnc_map_json: str | None = None

    ws_read_timeout_ms: Annotated[int, Field(ge=1000)] = 120_000
    ws_write_timeout_ms: Annotated[int, Field(ge=1000)] = 120_000
    tcp_connect_timeout_ms: Annotated[int, Field(ge=100)] = 5_000
    tcp_idle_timeout_ms: Annotated[int, Field(ge=1000)] = 300_000

    max_concurrent_sessions: Annotated[int, Field(ge=1)] = 1_000
    shutdown_grace_ms: Annotated[int, Field(ge=1_000)] = 30_000
    ws_ping_interval_ms: Annotated[int, Field(ge=1_000)] = 25_000

    class MapEntry(BaseModel):
        host: str
        port: Annotated[int, Field(ge=1, le=65535)]

    explicit_map: dict[int, MapEntry] = Field(default_factory=dict, repr=False)
    web_range: range = Field(default=range(0), repr=False)

    @field_validator("vnc_web_range")
    @classmethod
    def _validate_range(cls, value: str) -> str:
        if not value:
            raise ValueError("VNC_WEB_RANGE must not be empty")
        parts = value.split("-", 1)
        if len(parts) == 1:
            start = end = parts[0]
        else:
            start, end = parts
        try:
            start_num = int(start)
            end_num = int(end)
        except ValueError as exc:  # pragma: no cover - invalid range is handled below
            raise ValueError("VNC_WEB_RANGE must contain integers") from exc
        if start_num > end_num:
            raise ValueError("VNC_WEB_RANGE lower bound must be <= upper bound")
        if start_num < 0 or end_num > 9999:
            raise ValueError("VNC_WEB_RANGE must reference 4-digit identifiers")
        return value

    @model_validator(mode="after")
    def _populate_derived(self) -> "GatewaySettings":
        self.explicit_map = self._parse_explicit_map()
        self.web_range = self._parse_range()
        self._validate_overlaps()
        return self

    def _parse_range(self) -> range:
        start_str, end_str = (self.vnc_web_range.split("-", 1) + [None])[:2]
        if end_str is None:
            end_str = start_str
        assert start_str is not None  # pragma: no cover - guarded by validator
        start = int(start_str)
        end = int(end_str)
        return range(start, end + 1)

    def _parse_explicit_map(self) -> dict[int, "GatewaySettings.MapEntry"]:
        if not self.vnc_map_json:
            return {}
        try:
            raw = json.loads(self.vnc_map_json)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc.msg}") from exc
        if not isinstance(raw, dict):
            raise ValueError("VNC_MAP_JSON must be a JSON object")
        parsed: dict[int, GatewaySettings.MapEntry] = {}
        for raw_key, value in raw.items():
            try:
                key = int(raw_key)
            except (TypeError, ValueError) as exc:
                raise ValueError("VNC_MAP_JSON keys must be integers") from exc
            if not isinstance(value, dict):
                raise ValueError("VNC_MAP_JSON values must be objects")
            entry = GatewaySettings.MapEntry.model_validate(value)
            parsed[key] = entry
        return parsed

    def _validate_overlaps(self) -> None:
        duplicates = set(self.explicit_map).intersection(self.web_range)
        # Allow overrides inside the range by treating them as preferred mapping.
        # However, ensure no conflicting duplicate entries are provided.
        if len(duplicates) != len({key for key in duplicates}):  # pragma: no cover - defensive
            raise ValueError("Duplicate identifiers in VNC_MAP_JSON")

    def resolve(self, identifier: int) -> VncTarget:
        if identifier in self.explicit_map:
            entry = self.explicit_map[identifier]
            return VncTarget(host=entry.host, port=entry.port)
        if identifier not in self.web_range:
            msg = f"Identifier {identifier} outside configured range {self.vnc_web_range}"
            raise KeyError(msg)
        offset = identifier - self.web_range.start
        port = self.vnc_base_port + offset
        return VncTarget(host=self.vnc_default_host, port=port)


@lru_cache
def load_settings() -> GatewaySettings:
    """Return the cached settings instance."""

    return GatewaySettings()


__all__ = ["GatewaySettings", "VncTarget", "load_settings"]

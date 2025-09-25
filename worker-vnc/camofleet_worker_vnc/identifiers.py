"""Helpers for extracting VNC identifiers from requests."""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from fastapi import Request, WebSocket

_PREFIX_PATTERN = re.compile(r"/vnc/(?P<id>\d+)(?:/|$)")


def _normalize_candidate(value: Any) -> int | None:
    """Normalize a raw identifier value to an ``int`` when possible."""

    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def extract_identifier_from_path_params(params: Mapping[str, Any] | None) -> int | None:
    """Extract an identifier from ASGI path parameters."""

    if not params:
        return None
    return _normalize_candidate(params.get("identifier"))


def _iter_prefix_values(raw_prefix: str | None) -> Iterable[str]:
    if not raw_prefix:
        return []
    for value in raw_prefix.split(","):
        value = value.strip()
        if value:
            yield value


def extract_identifier_from_prefix(raw_prefix: str | None) -> int | None:
    """Extract an identifier from an ``X-Forwarded-Prefix`` header value."""

    for prefix in _iter_prefix_values(raw_prefix):
        match = _PREFIX_PATTERN.search(prefix)
        if match:
            return int(match.group("id"))
    return None


def extract_identifier_from_path(path: str) -> int | None:
    """Extract an identifier from the original URL path."""

    match = _PREFIX_PATTERN.search(path)
    if match:
        return int(match.group("id"))
    return None


RequestOrWebSocket = Request | WebSocket


def extract_identifier(request: RequestOrWebSocket) -> int | None:
    """Try to extract the VNC identifier from request metadata."""

    identifier = extract_identifier_from_prefix(request.headers.get("X-Forwarded-Prefix"))
    if identifier is not None:
        return identifier

    token = request.query_params.get("token") or request.query_params.get("id")
    token_identifier = _normalize_candidate(token)
    if token_identifier is not None:
        return token_identifier

    path_params_identifier = extract_identifier_from_path_params(getattr(request, "path_params", None))
    if path_params_identifier is not None:
        return path_params_identifier

    return extract_identifier_from_path(request.url.path)


__all__ = [
    "extract_identifier",
    "extract_identifier_from_path",
    "extract_identifier_from_prefix",
    "extract_identifier_from_path_params",
]

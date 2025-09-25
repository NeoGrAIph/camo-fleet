"""Helpers for extracting VNC identifiers from requests."""

from __future__ import annotations

import re
from typing import Iterable

from fastapi import Request, WebSocket

_PREFIX_PATTERN = re.compile(r"/vnc/(?P<id>\d+)(?:/|$)")


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
    if token and token.isdigit():
        return int(token)

    return extract_identifier_from_path(request.url.path)


__all__ = [
    "extract_identifier",
    "extract_identifier_from_path",
    "extract_identifier_from_prefix",
]

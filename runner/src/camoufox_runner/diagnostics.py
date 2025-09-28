"""Utilities for probing network capabilities inside the runner container."""

from __future__ import annotations

import asyncio
import logging
import ssl
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx

try:  # pragma: no cover - optional dependency guard
    from aioquic.asyncio.client import connect as quic_connect
    from aioquic.h3.connection import H3_ALPN
    from aioquic.quic.configuration import QuicConfiguration

    _AIOQUIC_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency guard
    _AIOQUIC_AVAILABLE = False

LOGGER = logging.getLogger(__name__)

ProbeStatus = Literal["ok", "error", "skipped"]


@dataclass(slots=True)
class ProbeOutcome:
    """Result of a single protocol probe."""

    status: ProbeStatus
    detail: str

    def asdict(self) -> dict[str, str]:
        """Return a serialisable representation."""

        return {"status": self.status, "detail": self.detail}


async def probe_http2(url: str, *, timeout: float) -> ProbeOutcome:
    """Attempt to perform an HTTP/2 GET against ``url``."""

    try:
        async with httpx.AsyncClient(http2=True, timeout=timeout) as client:
            response = await client.get(url, headers={"user-agent": "CamoufoxDiagnostics/1.0"})
        detail = f"{response.http_version} {response.status_code}"
        return ProbeOutcome("ok", detail)
    except Exception as exc:  # pragma: no cover - network failures are environment dependent
        return ProbeOutcome("error", repr(exc))


async def probe_http3(url: str, *, timeout: float) -> ProbeOutcome:
    """Attempt to establish an HTTP/3-capable QUIC session to ``url``."""

    if not _AIOQUIC_AVAILABLE:
        return ProbeOutcome("skipped", "aioquic is unavailable")

    parsed = urlparse(url)
    if parsed.scheme != "https":
        return ProbeOutcome("skipped", "HTTP/3 requires an https URL")
    if not parsed.hostname:
        return ProbeOutcome("error", "URL does not contain a hostname")

    host = parsed.hostname
    port = parsed.port or 443

    configuration = QuicConfiguration(is_client=True, alpn_protocols=H3_ALPN)
    configuration.verify_mode = ssl.CERT_REQUIRED
    configuration.server_name = host

    try:
        async with asyncio.timeout(timeout):
            async with quic_connect(host, port, configuration=configuration, wait_connected=True) as protocol:
                tls_state = getattr(protocol._quic, "tls", None)
                negotiated = getattr(tls_state, "alpn_negotiated", None)
                detail = f"ALPN={negotiated or 'unknown'}"
                return ProbeOutcome("ok", detail)
    except Exception as exc:  # pragma: no cover - depends on network policies
        return ProbeOutcome("error", repr(exc))


async def probe_target(url: str, *, timeout: float, logger: logging.Logger | None = None) -> dict[str, dict[str, str]]:
    """Run all supported probes for ``url`` and return their outcomes."""

    log = logger or LOGGER
    log.debug("Running network diagnostics for %s", url)
    http2_result = await probe_http2(url, timeout=timeout)
    http3_result = await probe_http3(url, timeout=timeout)
    result = {
        "http2": http2_result.asdict(),
        "http3": http3_result.asdict(),
    }
    log.info(
        "Diagnostics for %s: HTTP/2 %s, HTTP/3 %s",
        url,
        http2_result.status,
        http3_result.status,
    )
    return result


async def run_network_diagnostics(
    urls: list[str], *, timeout: float, logger: logging.Logger | None = None
) -> dict[str, dict[str, dict[str, str]]]:
    """Execute diagnostics for all ``urls`` concurrently."""

    if not urls:
        return {}

    tasks = [probe_target(url, timeout=timeout, logger=logger) for url in urls]
    results = await asyncio.gather(*tasks)
    return dict(zip(urls, results))


__all__ = ["run_network_diagnostics", "probe_http2", "probe_http3"]

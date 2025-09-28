from __future__ import annotations

import asyncio

import pytest

from camoufox_runner import diagnostics


def test_probe_http2_skips_when_httpx_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(diagnostics, "httpx", None)

    async def _run() -> diagnostics.ProbeOutcome:
        return await diagnostics.probe_http2("https://example.org", timeout=1.0)

    result = asyncio.run(_run())

    assert result.status == "skipped"
    assert "httpx is unavailable" in result.detail


def test_probe_http2_skips_when_http2_dependencies_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    class DummyAsyncClient:
        def __init__(self, *_, **__):
            pass

        async def __aenter__(self) -> DummyAsyncClient:
            return self

        async def __aexit__(self, *_) -> None:
            return None

        async def get(self, *_, **__):
            raise AssertionError("HTTP request should not be performed when dependencies missing")

    monkeypatch.setattr(diagnostics, "httpx", type("DummyHttpx", (), {"AsyncClient": DummyAsyncClient}))
    monkeypatch.setattr(diagnostics, "_HTTP2_AVAILABLE", False)

    async def _run() -> diagnostics.ProbeOutcome:
        return await diagnostics.probe_http2("https://example.org", timeout=1.0)

    result = asyncio.run(_run())

    assert result.status == "skipped"
    assert "HTTP/2 dependencies" in result.detail

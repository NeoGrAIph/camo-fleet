from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest
import pytest_asyncio

from camofleet_worker.config import SessionDefaults, WorkerSettings
from camofleet_worker.sessions import SessionManager


class DummyBrowserFactory:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[dict[str, Any]] = []

    async def launch(self, headless: bool) -> "DummyServer":
        self.calls.append({"headless": headless})
        return DummyServer(self.name)


class DummyPlaywright:
    def __init__(self) -> None:
        self.chromium = DummyBrowserFactory("chromium")
        self.firefox = DummyBrowserFactory("firefox")
        self.webkit = DummyBrowserFactory("webkit")


class DummyServer:
    def __init__(self, browser_name: str) -> None:
        self.browser_name = browser_name
        self.ws_endpoint = f"ws://{browser_name}/endpoint"
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest_asyncio.fixture()
async def manager(monkeypatch: pytest.MonkeyPatch) -> SessionManager:
    settings = WorkerSettings(
        session_defaults=SessionDefaults(idle_ttl_seconds=30, browser="chromium", headless=True),
        cleanup_interval=1,
    )
    playwright = DummyPlaywright()

    async def fake_launch(self: SessionManager, browser_name: str, *, headless: bool) -> DummyServer:
        factory = getattr(self._playwright, browser_name)
        return await factory.launch(headless=headless)

    monkeypatch.setattr(SessionManager, "_launch_browser_server", fake_launch)

    mgr = SessionManager(settings, playwright)
    await mgr.start()
    yield mgr
    await mgr.close()


@pytest.mark.asyncio()
async def test_create_session_uses_defaults(manager: SessionManager) -> None:
    detail = await manager.create({})
    assert detail.browser_name == "chromium"
    assert manager._playwright.chromium.calls[0]["headless"] is True


@pytest.mark.asyncio()
async def test_session_expires(manager: SessionManager) -> None:
    detail = await manager.create({})
    handle = await manager.get(detail.id)
    assert handle is not None
    handle.last_seen_at = datetime.now(tz=timezone.utc).replace(microsecond=0)
    handle.idle_ttl_seconds = 1
    await asyncio.sleep(1.1)
    await manager._cleanup_expired()
    assert await manager.get(detail.id) is None


@pytest.mark.asyncio()
async def test_ws_endpoint_base_override(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = WorkerSettings(
        session_defaults=SessionDefaults(idle_ttl_seconds=30, browser="chromium", headless=True),
        cleanup_interval=1,
        ws_endpoint_base="ws://public.example",
    )
    playwright = DummyPlaywright()

    async def fake_launch(self: SessionManager, browser_name: str, *, headless: bool) -> DummyServer:
        factory = getattr(self._playwright, browser_name)
        return await factory.launch(headless=headless)

    monkeypatch.setattr(SessionManager, "_launch_browser_server", fake_launch)

    mgr = SessionManager(settings, playwright)
    await mgr.start()
    try:
        handle = await mgr.create({})
        assert mgr.ws_endpoint_for(handle).startswith("ws://public.example")
        detail = handle.detail(mgr.vnc_payload_for(handle), mgr.ws_endpoint_for(handle))
        assert detail.ws_endpoint.startswith("ws://public.example")
    finally:
        await mgr.close()

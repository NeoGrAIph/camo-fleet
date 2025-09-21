from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from camoufox_runner.cleanup import IdleSessionCleaner
from camoufox_runner.prewarm import PrewarmPool
from camoufox_runner.vnc import VncSession, VncSlot


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _StubServer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.ws_endpoint = f"ws://{name}"
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _StubLauncher:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.servers: list[_StubServer] = []

    async def launch(self, *, headless: bool, vnc: bool, display: str | None, **_: object) -> _StubServer:
        server = _StubServer(f"server-{len(self.servers)}")
        self.calls.append({"headless": headless, "vnc": vnc, "display": display})
        self.servers.append(server)
        return server


class _StubVncManager:
    def __init__(self) -> None:
        self.available = True
        self.started: list[VncSession] = []
        self.stopped: list[VncSession | None] = []
        self._counter = 0

    async def start_session(self) -> VncSession:
        slot = VncSlot(display=100 + self._counter, vnc_port=5900 + self._counter, ws_port=6900 + self._counter)
        session = VncSession(
            slot=slot,
            display=f":{slot.display}",
            http_url="http://example/vnc",
            ws_url="ws://example/websockify",
            processes=[],
            drain_tasks=[],
        )
        self._counter += 1
        self.started.append(session)
        return session

    async def stop_session(self, session: VncSession | None) -> None:
        self.stopped.append(session)


@pytest.mark.anyio
async def test_prewarm_pool_replenishes_resources() -> None:
    launcher = _StubLauncher()
    vnc_manager = _StubVncManager()
    pool = PrewarmPool(
        launcher=launcher,
        vnc_manager=vnc_manager,
        headless_target=1,
        vnc_target=1,
        check_interval=0.01,
    )

    await pool.start()
    # Initial fill happens during start()
    first_headless = await pool.acquire(vnc=False, headless=True)
    first_vnc = await pool.acquire(vnc=True, headless=False)
    assert first_headless is not None
    assert first_vnc is not None
    assert first_vnc.vnc_session in vnc_manager.started

    pool.schedule_top_up()
    await asyncio.sleep(0.05)

    second_headless = await pool.acquire(vnc=False, headless=True)
    assert second_headless is not None

    # Pretend the caller consumed the prewarmed resources for real sessions.
    await first_headless.server.close()
    await vnc_manager.stop_session(None)
    await first_vnc.server.close()
    await vnc_manager.stop_session(first_vnc.vnc_session)
    await second_headless.server.close()
    await vnc_manager.stop_session(None)

    await pool.close()

    assert all(server.closed for server in launcher.servers)
    assert any(session is None for session in vnc_manager.stopped)
    assert any(session is not None for session in vnc_manager.stopped)


@dataclass
class _Handle:
    id: str


@pytest.mark.anyio
async def test_idle_session_cleaner_runs_once_and_background_loop() -> None:
    expired_handles = [_Handle("one"), _Handle("two")]
    seen: list[str] = []

    async def collect() -> list[_Handle]:
        return list(expired_handles)

    async def on_expired(handle: _Handle) -> None:
        seen.append(handle.id)

    cleaner = IdleSessionCleaner(interval=0.01, collect_expired=collect, on_expired=on_expired)

    await cleaner.run_once()
    assert seen == ["one", "two"]

    # Ensure the background loop schedules cleanups as well
    seen.clear()
    cleaner.start()
    await asyncio.sleep(0.03)
    await cleaner.stop()

    assert seen.count("one") >= 1
    assert seen.count("two") >= 1

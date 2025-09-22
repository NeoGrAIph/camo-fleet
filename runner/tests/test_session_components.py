from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from camoufox_runner.cleanup import IdleSessionCleaner
from camoufox_runner.config import RunnerSettings
from camoufox_runner.prewarm import PrewarmPool, PrewarmedResource
from camoufox_runner.sessions import SessionManager
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


class _BlockingLauncher(_StubLauncher):
    def __init__(self) -> None:
        super().__init__()
        self._block_next = False
        self.launch_started = asyncio.Event()
        self._resume_launch: asyncio.Future[None] | None = None
        self.cancelled_during_launch = False

    def block_next_launch(self) -> None:
        self._block_next = True
        self.launch_started.clear()

    def allow_launch(self) -> None:
        if self._resume_launch and not self._resume_launch.done():
            self._resume_launch.set_result(None)

    async def launch(self, *, headless: bool, vnc: bool, display: str | None, **kwargs: object) -> _StubServer:
        if self._block_next:
            self._block_next = False
            self.launch_started.set()
            loop = asyncio.get_running_loop()
            self._resume_launch = loop.create_future()
            try:
                await self._resume_launch
            except asyncio.CancelledError:
                self.cancelled_during_launch = True
                raise
            finally:
                self._resume_launch = None
        return await super().launch(headless=headless, vnc=vnc, display=display, **kwargs)


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


class _StubPlaywright:
    class _Firefox:
        async def connect(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("connect should not be used in this test")

    def __init__(self) -> None:
        self.firefox = self._Firefox()


async def _wait_for_counts(
    pool: PrewarmPool, *, headless: int, vnc: int, timeout: float = 0.5
) -> None:
    async def _wait_loop() -> None:
        while True:
            async with pool._lock:  # type: ignore[attr-defined]
                if len(pool._headless) == headless and len(pool._vnc) == vnc:  # type: ignore[attr-defined]
                    return
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_wait_loop(), timeout)


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
    try:
        # Initial fill happens during start()
        first_headless = await pool.acquire(vnc=False, headless=True)
        first_vnc = await pool.acquire(vnc=True, headless=False)
        assert first_headless is not None
        assert first_vnc is not None
        assert first_vnc.vnc_session in vnc_manager.started

        pool.schedule_top_up()
        await _wait_for_counts(pool, headless=1, vnc=1)

        second_headless = await pool.acquire(vnc=False, headless=True)
        assert second_headless is not None

        # Pretend the caller consumed the prewarmed resources for real sessions.
        await first_headless.server.close()
        await vnc_manager.stop_session(None)
        await first_vnc.server.close()
        await vnc_manager.stop_session(first_vnc.vnc_session)
        await second_headless.server.close()
        await vnc_manager.stop_session(None)
    finally:
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


@pytest.mark.anyio
async def test_session_manager_restores_prewarm_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    launcher = _StubLauncher()
    vnc_manager = _StubVncManager()

    monkeypatch.setattr("camoufox_runner.sessions.BrowserLauncher", lambda **_: launcher)
    monkeypatch.setattr(
        "camoufox_runner.sessions.VncProcessManager",
        lambda *args, **kwargs: vnc_manager,
    )

    settings = RunnerSettings(
        prewarm_headless=2,
        prewarm_vnc=1,
        prewarm_check_interval_seconds=0.11,
    )
    manager = SessionManager(settings, _StubPlaywright())

    await manager.start()
    try:
        await _wait_for_counts(manager._prewarm, headless=2, vnc=1)

        for _ in range(3):
            handle = await manager.create({"headless": True})
            await manager.delete(handle.id)
            await _wait_for_counts(manager._prewarm, headless=2, vnc=1)

        for _ in range(2):
            handle = await manager.create({"vnc": True})
            await manager.delete(handle.id)
            await _wait_for_counts(manager._prewarm, headless=2, vnc=1)

        async with manager._prewarm._lock:  # type: ignore[attr-defined]
            assert len(manager._prewarm._headless) == 2  # type: ignore[attr-defined]
            assert len(manager._prewarm._vnc) == 1  # type: ignore[attr-defined]
    finally:
        await manager.close()

    # Initial fill plus replenishments triggered by each session
    assert len(launcher.servers) >= 8


@pytest.mark.anyio
async def test_prewarm_pool_loop_recovers_after_drain() -> None:
    launcher = _StubLauncher()
    vnc_manager = _StubVncManager()
    pool = PrewarmPool(
        launcher=launcher,
        vnc_manager=vnc_manager,
        headless_target=2,
        vnc_target=0,
        check_interval=0.01,
    )

    consumed: list[PrewarmedResource] = []

    await pool.start()
    try:
        await _wait_for_counts(pool, headless=2, vnc=0)

        while resource := await pool.acquire(vnc=False, headless=True):
            consumed.append(resource)

        assert len(consumed) == 2

        await _wait_for_counts(pool, headless=2, vnc=0)
    finally:
        for item in consumed:
            await item.server.close()
            await vnc_manager.stop_session(item.vnc_session)
        await pool.close()


@pytest.mark.anyio
async def test_prewarm_pool_stop_cancels_blocked_launch() -> None:
    launcher = _BlockingLauncher()
    vnc_manager = _StubVncManager()
    pool = PrewarmPool(
        launcher=launcher,
        vnc_manager=vnc_manager,
        headless_target=1,
        vnc_target=0,
        check_interval=0.01,
    )

    await pool.start()
    close_called = False
    try:
        await _wait_for_counts(pool, headless=1, vnc=0)

        launcher.block_next_launch()
        pool._headless_target = 2  # type: ignore[attr-defined]

        await asyncio.wait_for(launcher.launch_started.wait(), 0.5)

        await asyncio.wait_for(pool.stop(), 0.5)
        assert launcher.cancelled_during_launch
        assert pool._task is None  # type: ignore[attr-defined]

        await asyncio.wait_for(pool.close(), 0.5)
        close_called = True
    finally:
        if not close_called:
            await pool.close()

    assert all(server.closed for server in launcher.servers)

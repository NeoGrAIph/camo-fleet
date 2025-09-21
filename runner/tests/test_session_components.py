from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from camoufox_runner.cleanup import CleanupScheduler, IdleTimeoutEvaluator
from camoufox_runner.config import RunnerSettings
from camoufox_runner.prewarm_pool import PrewarmPool


pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"

class FakeBrowserHandle:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeBrowserLauncher:
    def __init__(self) -> None:
        self.launched: list[dict[str, object]] = []
        self.handles: list[FakeBrowserHandle] = []

    async def launch(self, **kwargs):  # type: ignore[override]
        self.launched.append(kwargs)
        handle = FakeBrowserHandle()
        self.handles.append(handle)
        return handle


class FakeVncSession:
    def __init__(self, display: int) -> None:
        self.display = f":{display}"
        self.slot = SimpleNamespace(display=display, vnc_port=5900 + display, ws_port=6900 + display)
        self.http_url = f"http://example/{display}"
        self.ws_url = f"ws://example/{display}"
        self.stopped = False


class FakeVncManager:
    def __init__(self, *, available: bool = True) -> None:
        self.is_available = available
        self.sessions: list[FakeVncSession] = []

    async def start_session(self):  # type: ignore[override]
        session = FakeVncSession(len(self.sessions))
        self.sessions.append(session)
        return session

    async def stop_session(self, session):  # type: ignore[override]
        if session is None:
            return
        session.stopped = True


async def test_prewarm_pool_top_up_and_drain() -> None:
    settings = RunnerSettings(
        prewarm_headless=2,
        prewarm_vnc=1,
        prewarm_check_interval_seconds=0.2,
        vnc_display_min=1,
        vnc_display_max=1,
        vnc_port_min=5900,
        vnc_port_max=5902,
        vnc_ws_port_min=6900,
        vnc_ws_port_max=6902,
    )
    launcher = FakeBrowserLauncher()
    vnc_manager = FakeVncManager()
    pool = PrewarmPool(settings, launcher, vnc_manager)  # type: ignore[arg-type]

    await pool.top_up_once()
    assert len(launcher.handles) == 3

    acquired_headless = await pool.acquire(vnc=False, headless=True)
    assert acquired_headless is not None and acquired_headless.headless
    await acquired_headless.server.close()

    acquired_vnc = await pool.acquire(vnc=True, headless=False)
    assert acquired_vnc is not None and not acquired_vnc.headless
    await acquired_vnc.server.close()
    await vnc_manager.stop_session(acquired_vnc.vnc_session)

    await pool.close()

    assert all(handle.closed for handle in launcher.handles)
    assert all(session.stopped for session in vnc_manager.sessions)


async def test_prewarm_pool_disables_vnc_when_unavailable() -> None:
    settings = RunnerSettings(
        prewarm_headless=1,
        prewarm_vnc=3,
        prewarm_check_interval_seconds=0.2,
    )
    launcher = FakeBrowserLauncher()
    vnc_manager = FakeVncManager(available=False)
    pool = PrewarmPool(settings, launcher, vnc_manager)  # type: ignore[arg-type]

    await pool.top_up_once()

    assert len(launcher.launched) == 1  # only headless
    assert not vnc_manager.sessions


async def test_cleanup_scheduler_invokes_callback() -> None:
    called = asyncio.Event()

    async def _callback() -> None:
        called.set()

    scheduler = CleanupScheduler(interval=0.05, callback=_callback, name="test-cleanup")
    scheduler.start()
    await asyncio.wait_for(called.wait(), timeout=1)
    await scheduler.stop()


def test_idle_timeout_evaluator_marks_expired() -> None:
    now = datetime.now(tz=UTC)
    evaluator = IdleTimeoutEvaluator(clock=lambda: (now + timedelta(seconds=10)).timestamp())
    expired = evaluator.select_expired(
        [
            SimpleNamespace(last_seen_at=now, idle_ttl_seconds=5),
            SimpleNamespace(last_seen_at=now, idle_ttl_seconds=15),
        ]
    )
    assert len(expired) == 1

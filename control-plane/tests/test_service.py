from __future__ import annotations

import pytest

from camofleet_control.config import ControlSettings, WorkerConfig
from camofleet_control.main import create_app
from camofleet_control.service import (
    aclose_worker_clients,
    worker_client,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_worker_client_reuses_http_client() -> None:
    worker = WorkerConfig(name="shared", url="http://shared")
    settings = ControlSettings(workers=[worker])

    async with worker_client(worker, settings) as first:
        async with worker_client(worker, settings) as second:
            assert first.http_client is second.http_client

    await aclose_worker_clients()


@pytest.mark.anyio("asyncio")
async def test_worker_clients_closed_on_shutdown() -> None:
    worker = WorkerConfig(name="shutdown", url="http://shutdown")
    settings = ControlSettings(workers=[worker])
    app = create_app(settings)

    await app.router.startup()
    async with worker_client(worker, settings) as client:
        http_client = client.http_client

    assert not http_client.is_closed

    await app.router.shutdown()

    assert http_client.is_closed

    await aclose_worker_clients()

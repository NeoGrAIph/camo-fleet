import pytest

from runner.camoufox_runner.vnc import VncResourcePool


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_release_makes_slot_available_first():
    pool = VncResourcePool(displays=[1], vnc_ports=[5900], ws_ports=[6000])

    slot = await pool.acquire()

    await pool.release(slot)

    next_slot = await pool.acquire()

    assert next_slot == slot

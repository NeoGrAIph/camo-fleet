from __future__ import annotations

from typing import Any

from camofleet_control.config import ControlSettings
from camofleet_control.main import create_app
from camofleet_control.models import WorkerStatus
from fastapi.testclient import TestClient


async def fake_gather_worker_status(_: Any, __: ControlSettings) -> list[WorkerStatus]:
    """Return deterministic worker status data for diagnostics tests."""

    return [
        WorkerStatus(
            name="runner-1",
            healthy=True,
            supports_vnc=True,
            detail={
                "status": "ok",
                "checks": {"playwright": "ok"},
                "diagnostics": {
                    "status": "complete",
                    "results": {
                        "https://bot.sannysoft.com": {
                            "http2": {"status": "ok", "detail": "HTTP/2 200"},
                            "http3": {"status": "error", "detail": "PR_END_OF_FILE_ERROR"},
                        },
                    },
                },
            },
        ),
        WorkerStatus(
            name="runner-2",
            healthy=False,
            supports_vnc=False,
            detail={},
        ),
    ]


def test_diagnostics_endpoint(monkeypatch) -> None:
    """Ensure the diagnostics endpoint returns structured probe data."""

    monkeypatch.setattr(
        "camofleet_control.main.gather_worker_status", fake_gather_worker_status
    )

    app = create_app(ControlSettings(workers=[]))
    client = TestClient(app)

    response = client.post("/diagnostics")
    assert response.status_code == 200
    payload = response.json()

    assert payload["workers"][0]["name"] == "runner-1"
    probes = payload["workers"][0]["targets"][0]["probes"]
    assert probes[0]["protocol"] == "http2"
    assert probes[0]["status"] == "ok"
    assert probes[1]["status"] == "error"

    offline_worker = payload["workers"][1]
    assert offline_worker["diagnostics_status"] == "disabled"
    assert offline_worker["notes"]

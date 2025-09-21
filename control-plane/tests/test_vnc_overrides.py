from __future__ import annotations

from camofleet_control.config import WorkerConfig
from camofleet_control.main import apply_vnc_overrides


def test_apply_vnc_overrides_when_worker_provides_urls() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://public.example:7443",
        vnc_http="https://public.example",
    )
    payload = {
        "ws": "ws://internal-host:6901/novnc/?token=abc",
        "http": "http://internal-host:6901/vnc.html?foo=1",
        "password_protected": True,
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://public.example:7443/novnc/?token=abc"
    assert result["http"] == "https://public.example/vnc.html?foo=1"
    assert result["password_protected"] is True


def test_apply_vnc_overrides_when_worker_has_no_overrides() -> None:
    worker = WorkerConfig(name="plain", url="http://internal:8080")
    payload = {
        "ws": "ws://internal-host:6901/novnc/?token=abc",
        "http": "http://internal-host:6901/vnc.html?foo=1",
    }

    assert apply_vnc_overrides(worker, payload) == payload

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

    assert result["ws"] == "wss://public.example:6901/novnc/?token=abc"
    assert result["http"] == "https://public.example/vnc.html?foo=1"
    assert result["password_protected"] is True


def test_apply_vnc_overrides_when_worker_has_no_overrides() -> None:
    worker = WorkerConfig(name="plain", url="http://internal:8080")
    payload = {
        "ws": "ws://internal-host:6901/novnc/?token=abc",
        "http": "http://internal-host:6901/vnc.html?foo=1",
    }

    assert apply_vnc_overrides(worker, payload) == payload


def test_apply_vnc_overrides_supports_port_placeholder() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://edge-{port}.example",
        vnc_http="https://edge.example/view?external_port={port}",
    )
    payload = {
        "ws": "ws://internal-host:6901/novnc/?token=abc",
        "http": "http://internal-host:6901/vnc.html?foo=1",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://edge-6901.example/novnc/?token=abc"
    assert result["http"] == "https://edge.example/view?external_port=6901"


def test_apply_vnc_overrides_allows_path_placeholder() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://proxy.example:{port}/proxy/{port}",
        vnc_http="https://proxy.example/proxy/{port}",
    )
    payload = {
        "ws": "ws://internal-host:6905/novnc/?token=abc",
        "http": "http://internal-host:6905/vnc.html?foo=1",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://proxy.example:6905/proxy/6905?token=abc"
    assert result["http"] == "https://proxy.example/proxy/6905?foo=1"


def test_apply_vnc_overrides_preserves_credentials() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://user:pass@public.example",
        vnc_http="https://user:pass@public.example",
    )
    payload = {
        "ws": "ws://internal-host:6902/novnc/?token=abc",
        "http": "http://internal-host:6902/vnc.html?foo=1",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://user:pass@public.example/novnc/?token=abc"
    assert result["http"] == "https://user:pass@public.example/vnc.html?foo=1"


def test_apply_vnc_overrides_supports_host_placeholder() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://{host}/proxy",
        vnc_http="https://proxy.example/{host}/{port}",
    )
    payload = {
        "ws": "ws://internal-host:6903/novnc/?token=abc",
        "http": "http://internal-host:6903/vnc.html?foo=1",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://internal-host/proxy?token=abc"
    assert result["http"] == "https://proxy.example/internal-host/6903?foo=1"


def test_apply_vnc_overrides_does_not_append_port_without_placeholder() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://camofleet.services.synestra.tech/vnc/{port}/websockify",
        vnc_http="https://camofleet.services.synestra.tech/vnc/{port}/vnc.html",
    )
    payload = {
        "ws": "ws://internal-host:6904/websockify?token=abc",
        "http": "http://internal-host:6904/vnc.html?foo=1",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == (
        "wss://camofleet.services.synestra.tech/vnc/6904/websockify?token=abc"
    )
    assert result["http"] == (
        "https://camofleet.services.synestra.tech/vnc/6904/vnc.html?foo=1"
    )

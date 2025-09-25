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
        "ws": "ws://internal-host:6900/websockify?token=6901",
        "http": "http://internal-host:6900/vnc/6901",
        "password_protected": True,
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://public.example:7443/websockify?token=6901"
    assert result["http"] == "https://public.example/vnc/6901"
    assert result["password_protected"] is True


def test_apply_vnc_overrides_when_worker_has_no_overrides() -> None:
    worker = WorkerConfig(name="plain", url="http://internal:8080")
    payload = {
        "ws": "ws://internal-host:6900/websockify?token=6901",
        "http": "http://internal-host:6900/vnc/6901",
    }

    assert apply_vnc_overrides(worker, payload) == payload


def test_apply_vnc_overrides_supports_port_placeholder() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://edge-{id}.example",
        vnc_http="https://edge.example/view?external_port={id}",
    )
    payload = {
        "ws": "ws://internal-host:6900/websockify?token=6901",
        "http": "http://internal-host:6900/vnc/6901",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://edge-6901.example/websockify?token=6901"
    assert result["http"] == "https://edge.example/view?external_port=6901"


def test_apply_vnc_overrides_allows_path_placeholder() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://proxy.example:{port}/proxy/{id}",
        vnc_http="https://proxy.example/proxy/{id}",
    )
    payload = {
        "ws": "ws://internal-host:6900/websockify?token=6905",
        "http": "http://internal-host:6900/vnc/6905",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://proxy.example:6905/proxy/6905?token=6905"
    assert result["http"] == "https://proxy.example/proxy/6905"


def test_apply_vnc_overrides_preserves_credentials() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://user:pass@public.example",
        vnc_http="https://user:pass@public.example",
    )
    payload = {
        "ws": "ws://internal-host:6900/websockify?token=6902",
        "http": "http://internal-host:6900/vnc/6902",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://user:pass@public.example/websockify?token=6902"
    assert result["http"] == "https://user:pass@public.example/vnc/6902"


def test_apply_vnc_overrides_supports_host_placeholder() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://{host}/proxy",
        vnc_http="https://proxy.example/{host}/{id}",
    )
    payload = {
        "ws": "ws://internal-host:6900/websockify?token=6903",
        "http": "http://internal-host:6900/vnc/6903",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == "wss://internal-host/proxy?token=6903"
    assert result["http"] == "https://proxy.example/internal-host/6903"


def test_apply_vnc_overrides_does_not_append_port_without_placeholder() -> None:
    worker = WorkerConfig(
        name="vnc-worker",
        url="http://internal:8080",
        vnc_ws="wss://camofleet.services.synestra.tech/vnc/{id}/websockify",
        vnc_http="https://camofleet.services.synestra.tech/vnc/{id}",
    )
    payload = {
        "ws": "ws://internal-host:6900/websockify?token=6904",
        "http": "http://internal-host:6900/vnc/6904",
    }

    result = apply_vnc_overrides(worker, payload)

    assert result["ws"] == (
        "wss://camofleet.services.synestra.tech/vnc/6904/websockify?token=6904"
    )
    assert result["http"] == (
        "https://camofleet.services.synestra.tech/vnc/6904"
    )

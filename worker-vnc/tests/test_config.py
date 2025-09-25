from __future__ import annotations

import json

import pytest

from camofleet_worker_vnc.config import GatewaySettings, VncTarget


def test_default_mapping_range() -> None:
    settings = GatewaySettings()
    assert settings.resolve(6900) == VncTarget(host="127.0.0.1", port=5900)
    assert settings.resolve(6904) == VncTarget(host="127.0.0.1", port=5904)
    with pytest.raises(KeyError):
        settings.resolve(6899)


def test_explicit_mapping_overrides_default() -> None:
    mapping = json.dumps({"6901": {"host": "10.0.0.5", "port": 5999}})
    settings = GatewaySettings(vnc_map_json=mapping)
    target = settings.resolve(6901)
    assert target.host == "10.0.0.5"
    assert target.port == 5999


def test_range_and_base_port_are_applied() -> None:
    settings = GatewaySettings(vnc_web_range="7000-7002", vnc_base_port=6000)
    assert settings.resolve(7000).port == 6000
    assert settings.resolve(7002).port == 6002
    with pytest.raises(KeyError):
        settings.resolve(6999)


def test_invalid_map_json_raises() -> None:
    with pytest.raises(ValueError):
        GatewaySettings(vnc_map_json="not-json")

    with pytest.raises(ValueError):
        GatewaySettings(vnc_map_json=json.dumps({"bad": 1}))

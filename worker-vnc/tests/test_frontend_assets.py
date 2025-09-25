from __future__ import annotations

from camofleet_worker_vnc.app import STATIC_DIR


def test_viewer_builds_absolute_websocket_url() -> None:
    script = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    assert "/websockify?token=" in script
    assert "window.location.host" in script
    assert "window.location.protocol" in script

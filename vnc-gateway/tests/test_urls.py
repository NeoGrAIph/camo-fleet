from camofleet_vnc_gateway.main import _build_upstream_url, _join_paths


def test_build_upstream_url_with_prefix() -> None:
    result = _build_upstream_url(
        scheme="http",
        host="runner",
        port=6901,
        prefix="/vnc",
        path_suffix="/vnc.html",
        query="path=websockify",
    )

    assert result == "http://runner:6901/vnc/vnc.html?path=websockify"


def test_join_paths_handles_root() -> None:
    assert _join_paths("", "/websockify") == "/websockify"
    assert _join_paths("/prefix", "/") == "/prefix"

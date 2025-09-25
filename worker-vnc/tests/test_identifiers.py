from __future__ import annotations

from types import SimpleNamespace

import pytest

from camofleet_worker_vnc.identifiers import (
    extract_identifier,
    extract_identifier_from_path,
    extract_identifier_from_prefix,
)


class DummyUrl(SimpleNamespace):
    def __str__(self) -> str:  # pragma: no cover - defensive fallback
        return self.path


@pytest.mark.parametrize(
    "prefix, expected",
    [
        ("/vnc/6900", 6900),
        ("/foo/vnc/6901/", 6901),
        ("/foo,/vnc/6902", 6902),
        (None, None),
        ("/not/matching", None),
    ],
)
def test_extract_identifier_from_prefix(prefix: str | None, expected: int | None) -> None:
    assert extract_identifier_from_prefix(prefix) == expected


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/vnc/6903", 6903),
        ("/foo/vnc/6904/", 6904),
        ("/", None),
    ],
)
def test_extract_identifier_from_path(path: str, expected: int | None) -> None:
    assert extract_identifier_from_path(path) == expected


def test_extract_identifier_prefers_forwarded_prefix() -> None:
    request = SimpleNamespace(
        headers={"X-Forwarded-Prefix": "/vnc/6901"},
        query_params={"token": "6905"},
        url=DummyUrl(path="/vnc/6999"),
    )
    assert extract_identifier(request) == 6901


def test_extract_identifier_falls_back_to_query_token() -> None:
    request = SimpleNamespace(
        headers={},
        query_params={"token": "6902"},
        url=DummyUrl(path="/"),
    )
    assert extract_identifier(request) == 6902


def test_extract_identifier_uses_id_parameter() -> None:
    request = SimpleNamespace(
        headers={},
        query_params={"id": "6903"},
        url=DummyUrl(path="/"),
    )
    assert extract_identifier(request) == 6903


def test_extract_identifier_uses_path_as_last_resort() -> None:
    request = SimpleNamespace(
        headers={},
        query_params={},
        url=DummyUrl(path="/vnc/6904"),
    )
    assert extract_identifier(request) == 6904


def test_extract_identifier_uses_path_params() -> None:
    request = SimpleNamespace(
        headers={},
        query_params={},
        path_params={"identifier": "6905"},
        url=DummyUrl(path="/"),
    )
    assert extract_identifier(request) == 6905


def test_extract_identifier_returns_none_when_missing() -> None:
    request = SimpleNamespace(headers={}, query_params={}, url=DummyUrl(path="/"))
    assert extract_identifier(request) is None

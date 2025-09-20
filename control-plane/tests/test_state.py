from __future__ import annotations

import pytest

from camofleet_control.config import ControlSettings, WorkerConfig
from camofleet_control.main import AppState
from fastapi import HTTPException


def make_settings(workers: list[WorkerConfig]) -> ControlSettings:
    return ControlSettings(workers=workers)


def test_pick_worker_round_robin() -> None:
    workers = [
        WorkerConfig(name="a", url="http://a"),
        WorkerConfig(name="b", url="http://b"),
    ]
    state = AppState(make_settings(workers))
    assert state.pick_worker().name == "a"
    assert state.pick_worker().name == "b"
    assert state.pick_worker().name == "a"


def test_pick_worker_by_name() -> None:
    workers = [WorkerConfig(name="x", url="http://x")]
    state = AppState(make_settings(workers))
    assert state.pick_worker("x").name == "x"
    with pytest.raises(HTTPException):
        state.pick_worker("missing")

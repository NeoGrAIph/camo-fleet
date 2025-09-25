from __future__ import annotations

import asyncio
import json
import threading
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from starlette import status

from camofleet_worker_vnc.app import create_app
from camofleet_worker_vnc.config import GatewaySettings


@pytest.fixture()
def app() -> TestClient:
    settings = GatewaySettings()
    application = create_app(settings)
    with TestClient(application) as client:
        yield client


def test_index_injects_identifier_from_header(app: TestClient) -> None:
    response = app.get("/", headers={"X-Forwarded-Prefix": "/vnc/6901"})
    assert response.status_code == 200
    assert "window.__VNC_ID__ = '6901'" in response.text
    assert '<script type="module" src="/static/viewer.js"></script>' in response.text


def test_index_returns_error_when_identifier_missing(app: TestClient) -> None:
    response = app.get("/")
    assert response.status_code == 400
    assert "identifier was not provided" in response.text


def test_websockify_status_endpoint(app: TestClient) -> None:
    ok_response = app.get("/websockify", headers={"X-Forwarded-Prefix": "/vnc/6900"})
    assert ok_response.status_code == 200
    assert ok_response.json() == {"status": "ready"}

    missing = app.get("/websockify")
    assert missing.status_code == 400
    assert missing.json()["error"] == "missing_id"

    unknown = app.get("/websockify?id=6999")
    assert unknown.status_code == 404
    assert unknown.json()["error"] == "unknown_id"


@contextmanager
def _start_echo_server(host: str = "127.0.0.1"):
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    server_holder: dict[str, asyncio.AbstractServer] = {}

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while data := await reader.read(32_768):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def runner() -> None:
        server = await asyncio.start_server(handle, host, 0)
        server_holder["server"] = server
        ready.set()

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    try:
        asyncio.run_coroutine_threadsafe(runner(), loop).result(timeout=5)
        ready.wait(timeout=5)
        server = server_holder["server"]
        sockets = server.sockets
        assert sockets is not None
        port = sockets[0].getsockname()[1]
        yield host, port
    finally:
        server = server_holder.get("server")
        if server is not None:
            async def shutdown() -> None:
                server.close()
                await server.wait_closed()

            asyncio.run_coroutine_threadsafe(shutdown(), loop).result(timeout=5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)


def test_websocket_proxy_roundtrip() -> None:
    with _start_echo_server() as (host, port):
        settings = GatewaySettings(
            vnc_map_json=json.dumps({"6901": {"host": host, "port": port}}),
            ws_read_timeout_ms=10_000,
            ws_write_timeout_ms=10_000,
            tcp_idle_timeout_ms=60_000,
            ws_ping_interval_ms=10_000,
        )
        app = create_app(settings)
        with TestClient(app) as client:
            with client.websocket_connect("/websockify?token=6901") as websocket:
                payload = b"hello"
                websocket.send_bytes(payload)
                received = websocket.receive_bytes()
                assert received == payload


def test_websocket_unknown_identifier_closes_connection() -> None:
    app = create_app(GatewaySettings())
    with TestClient(app) as client:
        with client.websocket_connect("/websockify?token=6999") as websocket:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_text()
            assert excinfo.value.code == status.WS_1008_POLICY_VIOLATION

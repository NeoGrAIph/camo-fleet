"""FastAPI application that proxies VNC traffic through fixed ports."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Iterable
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
import websockets
from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK

from .config import GatewaySettings, load_settings

LOGGER = logging.getLogger(__name__)

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


class GatewayState:
    """Mutable objects shared across request handlers."""

    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(timeout=settings.request_timeout)

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    async def close(self) -> None:
        await self._client.aclose()


def create_app(settings: GatewaySettings | None = None) -> FastAPI:
    """Instantiate the FastAPI application that powers the gateway."""

    cfg = settings or load_settings()
    app = FastAPI(title="Camofleet VNC Gateway", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        allow_credentials=True,
    )

    state = GatewayState(cfg)

    @app.on_event("shutdown")
    async def _shutdown() -> None:  # pragma: no cover - FastAPI lifecycle
        await state.close()

    def get_state() -> GatewayState:
        return state

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def _proxy_http(
        request: Request,
        *,
        state: GatewayState,
        path_suffix: str,
    ) -> Response:
        try:
            port = state.settings.validate_port(request.query_params.get("target_port"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        query_items = [
            (key, value) for key, value in request.query_params.multi_items() if key != "target_port"
        ]
        query_string = urlencode(query_items)

        upstream_url = _build_upstream_url(
            scheme=state.settings.runner_http_scheme,
            host=state.settings.runner_host,
            port=port,
            prefix=state.settings.normalised_prefix(),
            path_suffix=path_suffix,
            query=query_string,
        )

        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        body = await request.body()
        try:
            response = await state.client.request(
                request.method,
                upstream_url,
                headers=headers,
                content=body if body else None,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        return Response(
            content=response.content,
            status_code=response.status_code,
            headers={
                key: value
                for key, value in response.headers.items()
                if key.lower() not in HOP_BY_HOP_HEADERS
            },
        )

    @app.api_route("/vnc", methods=["GET", "HEAD", "OPTIONS"])
    async def proxy_root(request: Request, state: GatewayState = Depends(get_state)) -> Response:
        return await _proxy_http(request, state=state, path_suffix="/")

    @app.api_route("/vnc/{path:path}", methods=["GET", "HEAD", "OPTIONS"])
    async def proxy_http(
        path: str,
        request: Request,
        state: GatewayState = Depends(get_state),
    ) -> Response:
        suffix = f"/{path}" if path else "/"
        return await _proxy_http(request, state=state, path_suffix=suffix)

    @app.websocket("/vnc/websockify")
    async def proxy_websocket(websocket: WebSocket, state: GatewayState = Depends(get_state)) -> None:
        try:
            port = state.settings.validate_port(websocket.query_params.get("target_port"))
        except ValueError as exc:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=str(exc))
            return

        query_items = [
            (key, value)
            for key, value in websocket.query_params.multi_items()
            if key != "target_port"
        ]
        query_string = urlencode(query_items)

        upstream_url = _build_upstream_url(
            scheme=state.settings.runner_ws_scheme,
            host=state.settings.runner_host,
            port=port,
            prefix=state.settings.normalised_prefix(),
            path_suffix="/websockify",
            query=query_string,
        )

        subprotocol_header = websocket.headers.get("sec-websocket-protocol")
        subprotocols = [
            item.strip()
            for item in (subprotocol_header.split(",") if subprotocol_header else [])
            if item.strip()
        ]

        extra_headers = _select_upstream_headers(websocket.headers.items())

        try:
            connect_ctx = websockets.connect(
                upstream_url,
                ping_interval=None,
                subprotocols=subprotocols or None,
                extra_headers=extra_headers,
            )
            upstream = await connect_ctx.__aenter__()
            try:
                await websocket.accept(subprotocol=upstream.subprotocol)

                client_to_upstream = asyncio.create_task(
                    _forward_client_to_upstream(websocket, upstream),
                    name="vnc-gateway-client->upstream",
                )
                upstream_to_client = asyncio.create_task(
                    _forward_upstream_to_client(websocket, upstream),
                    name="vnc-gateway-upstream->client",
                )
                done, pending = await asyncio.wait(
                    {client_to_upstream, upstream_to_client},
                    return_when=asyncio.FIRST_EXCEPTION,
                )
                for task in pending:
                    task.cancel()
                for task in done:
                    exc = task.exception()
                    if exc:
                        raise exc
            finally:
                await connect_ctx.__aexit__(None, None, None)
        except (ConnectionClosedError, ConnectionClosedOK):
            with contextlib.suppress(RuntimeError):
                await websocket.close()
        except Exception as exc:  # pragma: no cover - defensive logging path
            LOGGER.warning("WebSocket proxy failure: %s", exc)
            with contextlib.suppress(RuntimeError):
                await websocket.close(code=status.WS_1011_INTERNAL_ERROR)

    return app


def _build_upstream_url(
    *,
    scheme: str,
    host: str,
    port: int,
    prefix: str,
    path_suffix: str,
    query: str,
) -> str:
    path_suffix = path_suffix or "/"
    combined_path = _join_paths(prefix, path_suffix)
    if not combined_path.startswith("/"):
        combined_path = f"/{combined_path}"
    query_part = f"?{query}" if query else ""
    return f"{scheme}://{host}:{port}{combined_path}{query_part}"


def _join_paths(prefix: str, suffix: str) -> str:
    prefix = (prefix or "").rstrip("/")
    suffix = suffix.lstrip("/")
    if prefix and suffix:
        return f"{prefix}/{suffix}"
    if prefix:
        return prefix
    if suffix:
        return f"/{suffix}"
    return "/"


def _select_upstream_headers(headers: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    allowed = {"origin", "user-agent", "cookie", "sec-websocket-extensions"}
    return [(key, value) for key, value in headers if key.lower() in allowed]


async def _forward_client_to_upstream(
    websocket: WebSocket, upstream: websockets.WebSocketClientProtocol
) -> None:
    try:
        while True:
            message = await websocket.receive()
            message_type = message.get("type")
            if message_type == "websocket.disconnect":
                await upstream.close()
                break
            if "text" in message and message["text"] is not None:
                await upstream.send(message["text"])
            elif "bytes" in message and message["bytes"] is not None:
                await upstream.send(message["bytes"])
    except Exception:
        await upstream.close()
        raise


async def _forward_upstream_to_client(
    websocket: WebSocket, upstream: websockets.WebSocketClientProtocol
) -> None:
    try:
        async for data in upstream:
            if isinstance(data, (bytes, bytearray)):
                await websocket.send_bytes(data)
            else:
                await websocket.send_text(data)
    finally:
        with contextlib.suppress(RuntimeError):
            await websocket.close()


__all__ = ["create_app"]

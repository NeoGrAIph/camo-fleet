# Code snippets per hop

## Control-plane override (UI discovery)
```python
    mutated = dict(payload)
    overrides = (("ws", worker.vnc_ws), ("http", worker.vnc_http))
    for key, override_template in overrides:
        original_url = payload.get(key)
        if not original_url or not override_template:
            continue

        try:
            parsed_original = urlparse(original_url)
        except ValueError:
            continue

        port_placeholder_used = "{port}" in override_template
        id_placeholder_used = "{id}" in override_template
        host_placeholder_used = "{host}" in override_template

        session_port = parsed_original.port
        if session_port is None:
            session_port = _default_port_for_scheme(parsed_original.scheme)

        identifier = _extract_vnc_identifier(parsed_original)
        effective_port = identifier if identifier is not None else session_port

        rendered_override = override_template
        if port_placeholder_used and effective_port is not None:
            rendered_override = rendered_override.replace("{port}", str(effective_port))
        if id_placeholder_used and identifier is not None:
            rendered_override = rendered_override.replace("{id}", str(identifier))
```【F:control-plane/camofleet_control/main.py†L470-L509】

## Gateway bridge (Traefik → runner)
```python
async def _proxy_websocket(websocket: WebSocket, target: VncTarget, settings: GatewaySettings) -> None:
    connect_timeout = _seconds_from_ms(settings.tcp_connect_timeout_ms)
    read_timeout = _seconds_from_ms(settings.ws_read_timeout_ms)
    write_timeout = _seconds_from_ms(settings.ws_write_timeout_ms)
    idle_timeout = _seconds_from_ms(settings.tcp_idle_timeout_ms)
    ping_interval = _seconds_from_ms(settings.ws_ping_interval_ms)

    async with asyncio.timeout(connect_timeout):
        reader, writer = await asyncio.open_connection(target.host, target.port)

    await websocket.accept(subprotocol=subprotocol)

    async def client_to_tcp() -> None:
        while True:
            message = await asyncio.wait_for(websocket.receive(), timeout=read_timeout)
            if message.get("type") == "websocket.disconnect":
                break
            if "ping" in message:
                await websocket._send({"type": "websocket.pong", "bytes": message.get("ping") or b""})
                continue
            data = message.get("bytes") or message.get("text", "").encode("utf-8")
            writer.write(data)
            await asyncio.wait_for(writer.drain(), timeout=write_timeout)
            activity_event.set()
```【F:worker-vnc/camofleet_worker_vnc/app.py†L100-L154】

## Runner session launch (TCP/RFB target)
```python
            x11vnc_cmd = [
                "x11vnc",
                "-display",
                display_name,
                "-shared",
                "-forever",
                "-rfbport",
                str(slot.vnc_port),
                "-localhost",
                "-nopw",
                "-quiet",
            ]
            x11vnc_proc, x11vnc_tasks = await self._spawn_process(
                x11vnc_cmd,
                name=f"vnc-x11vnc:{slot.display}",
            )

            http_url = self._compose_gateway_url(
                self._settings.vnc_http_base,
                slot.ws_port,
                kind="http",
            )
            ws_url = self._compose_gateway_url(
                self._settings.vnc_ws_base,
                slot.ws_port,
                kind="ws",
            )
```【F:runner/camoufox_runner/vnc.py†L135-L165】

## Traefik ingress (TLS termination)
```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: camofleet-worker-vnc
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`camofleet.example.com`) && PathPrefix(`/vnc/`)
      services:
        - name: camofleet-camo-fleet-worker-vnc
          port: 6900
  tls:
    secretName: camofleet-tls
```
【F:README.md†L169-L209】

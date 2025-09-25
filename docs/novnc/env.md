# Environment matrix

## Runner (`camoufox_runner`)

| Variable | Source | Default/sample | Purpose |
| --- | --- | --- | --- |
| `RUNNER_PORT` | `docker-compose.yml` | `8070` | HTTP API port exposed by the runner service and used by workers.【F:docker-compose.yml†L6-L19】 |
| `RUNNER_VNC_WS_BASE` | `.env.example`, runner config | `ws://vnc-gateway:6900` | Base WebSocket URL returned to clients (`{id}` is appended in `VncProcessManager`).【F:.env.example†L5-L13】【F:runner/camoufox_runner/config.py†L24-L45】 |
| `RUNNER_VNC_HTTP_BASE` | `.env.example`, runner config | `http://vnc-gateway:6900` | Base HTTP URL serving the noVNC page (used for iframe embedding).【F:.env.example†L5-L13】【F:runner/camoufox_runner/config.py†L24-L45】 |
| `RUNNER_VNC_DISPLAY_MIN/MAX` | `.env.example`, runner config | `100`–`199` | Allocated Xvfb display range for concurrent VNC sessions.【F:.env.example†L7-L13】【F:runner/camoufox_runner/config.py†L35-L43】 |
| `RUNNER_VNC_PORT_MIN/MAX` | `.env.example`, runner config | `5900`–`5999` | Raw RFB port range consumed by `x11vnc` per session.【F:.env.example†L9-L13】【F:runner/camoufox_runner/config.py†L37-L40】 |
| `RUNNER_VNC_WS_PORT_MIN/MAX` | `.env.example`, runner config | `6900`–`6999` | Identifier space that maps `{id}` to RFB ports via the gateway.【F:.env.example†L9-L13】【F:runner/camoufox_runner/config.py†L41-L42】 |
| `RUNNER_VNC_RESOLUTION` | `.env.example`, runner config | `1920x1080x24` | Resolution passed to Xvfb when spawning the VNC display.【F:.env.example†L13-L13】【F:runner/camoufox_runner/vnc.py†L120-L139】 |

## VNC gateway (`worker-vnc`)

| Variable | Source | Default/sample | Purpose |
| --- | --- | --- | --- |
| `VNC_DEFAULT_HOST` | `docker-compose.yml`, gateway config | `runner-vnc` (sample) / `127.0.0.1` default | Hostname the gateway connects to for RFB traffic.【F:docker-compose.yml†L22-L33】【F:worker-vnc/camofleet_worker_vnc/config.py†L25-L128】 |
| `VNC_WEB_RANGE` | `docker-compose.yml`, gateway config | `6900-6904` | Valid `{id}` range accepted on `/vnc/{id}`/`/websockify?token=`.【F:docker-compose.yml†L22-L33】【F:worker-vnc/camofleet_worker_vnc/config.py†L25-L128】 |
| `VNC_BASE_PORT` | `docker-compose.yml`, gateway config | `5900` | Starting RFB port; offset by `{id}` to reach the runner.【F:docker-compose.yml†L22-L33】【F:worker-vnc/camofleet_worker_vnc/config.py†L25-L128】 |
| `VNC_MAP_JSON` | Gateway config | `null` | Optional explicit map of `{id}` → host/port pairs for non-sequential routing.【F:worker-vnc/camofleet_worker_vnc/config.py†L25-L128】 |
| `WS_READ_TIMEOUT_MS` / `WS_WRITE_TIMEOUT_MS` | Gateway config | `120000` ms | WebSocket read/write budget before disconnecting idle clients.【F:worker-vnc/camofleet_worker_vnc/config.py†L25-L45】 |
| `TCP_IDLE_TIMEOUT_MS` | Gateway config | `300000` ms | Idle timeout applied while bridging TCP ↔ WebSocket.【F:worker-vnc/camofleet_worker_vnc/config.py†L25-L45】 |
| `WS_PING_INTERVAL_MS` | Gateway config | `25000` ms | Heartbeat cadence for client ping frames.【F:worker-vnc/camofleet_worker_vnc/config.py†L25-L45】 |

## Worker API (`camofleet_worker`)

| Variable | Source | Default/sample | Purpose |
| --- | --- | --- | --- |
| `WORKER_RUNNER_BASE_URL` | `docker-compose.yml`, worker config | `http://runner-headless:8070` (headless) / `http://runner-vnc:8070` (VNC) | Runner endpoint consumed by the worker for session lifecycle operations.【F:docker-compose.yml†L35-L58】【F:worker/camofleet_worker/config.py†L23-L40】 |
| `WORKER_SUPPORTS_VNC` | `docker-compose.yml`, worker config | `true`/`false` per service | Flags whether the worker advertises VNC support to the control-plane.【F:docker-compose.yml†L35-L58】【F:worker/camofleet_worker/config.py†L23-L40】 |
| `WORKER_SESSION_DEFAULTS__HEADLESS` | `docker-compose.yml`, worker config | `true` for headless worker | Controls the default `headless` flag for new sessions.【F:docker-compose.yml†L35-L42】【F:worker/camofleet_worker/config.py†L13-L37】 |

## Control-plane (`camofleet_control`)

| Variable | Source | Default/sample | Purpose |
| --- | --- | --- | --- |
| `CONTROL_PUBLIC_API_PREFIX` | `docker-compose.yml`, control config | `/api` (sample) / `/` default | Prepended to public REST and WebSocket URLs returned to the UI.【F:docker-compose.yml†L60-L78】【F:control-plane/camofleet_control/config.py†L26-L46】 |
| `CONTROL_WORKERS` | `.env.example`, control config | JSON array containing worker entries | Describes workers, including `supports_vnc` and the external `vnc_ws`/`vnc_http` URLs published to clients.【F:.env.example†L19-L27】【F:control-plane/camofleet_control/config.py†L26-L46】 |
| Worker override placeholders (`{id}`, `{host}`, `{port}`) | Control-plane runtime | Derived | Substituted by `apply_vnc_overrides` to rewrite internal runner URLs to public ingress addresses.【F:control-plane/camofleet_control/main.py†L470-L570】 |

## Docker image defaults

The VNC-enabled runner image bakes in additional defaults for local development, such as exposing ports `8070`, `5900`, and `6900` and enabling the `vnc-start.sh` legacy bridge when requested via `RUNNER_VNC_LEGACY`.【F:docker/Dockerfile.runner-vnc†L39-L65】【F:docker/scripts/runner-entrypoint.sh†L1-L16】 These values may be overridden at runtime via environment variables described above.

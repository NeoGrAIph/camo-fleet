# noVNC traffic guide

This guide documents how the Camouflage Fleet stack delivers a browser-based noVNC session over WSS, how Docker Compose wires the services together, and where Traefik terminates TLS before forwarding plain WebSocket traffic to the VNC gateway and runner.

## Component map

| Layer | Responsibility | Key implementation |
| --- | --- | --- |
| Browser / UI | Loads the control-plane API and renders the embedded noVNC client after reading the VNC descriptors returned by `CONTROL_WORKERS`. | Control-plane injects `http`/`ws` URLs per session when it applies worker overrides, ensuring the UI calls the public `/vnc/{id}` and `/websockify?token={id}` routes.【F:control-plane/camofleet_control/main.py†L470-L570】【F:deploy/helm/camo-fleet/templates/control-configmap.yaml†L1-L35】 |
| Traefik ingress | Terminates HTTPS/WSS on `websecure`, strips `/vnc/{id}` prefixes, and forwards both the HTML (`/vnc/{id}`) and WebSocket (`/websockify`) paths to the VNC gateway service listening on port 6900.【F:README.md†L152-L209】 |
| VNC gateway (`worker-vnc`) | FastAPI service serving the noVNC HTML, performing identifier lookup, enforcing capacity, and proxying WS frames to the runner via TCP while maintaining ping/idle timers.【F:worker-vnc/camofleet_worker_vnc/app.py†L100-L330】 |
| Runner (`camoufox_runner`) | Launches Xvfb+x11vnc, exposes RFB on `590x`, and composes gateway URLs (`http://…/vnc/{id}`, `ws://…/websockify?token={id}`) that flow back to the control-plane and UI.【F:runner/camoufox_runner/vnc.py†L120-L210】 |
| VNC server (`x11vnc`) | Speaks raw RFB to the runner’s browser container, secured behind the gateway and not exposed externally.【F:runner/camoufox_runner/vnc.py†L135-L155】 |

The Docker Compose topology wires these components together with explicit environment variables so the control-plane and runner know how to talk to the gateway and expose public URLs.【F:docker-compose.yml†L1-L87】

## Navigation

* [Sequence diagram](flow.mmd)
* [Route and port table](routes.csv)
* [Reverse-proxy extracts](reverse-proxy.md)
* [Environment matrix](env.md)
* [Code snippets per hop](code-snippets.md)
* [Repository inventory](repo-inventory.txt)
* [Verification artifacts](../../artifacts/novnc) — `traefik-access.log`, `websockify.log`, `vnc.tcpdump.pcap`

## Traffic walkthrough

1. **Session discovery:** The control-plane fetches worker session data, rewrites `vnc.ws`/`vnc.http` using overrides such as `wss://camofleet.example.com/websockify?token={id}`, and returns these URLs to the UI.【F:control-plane/camofleet_control/main.py†L470-L570】【F:deploy/helm/camo-fleet/templates/control-configmap.yaml†L1-L35】
2. **TLS termination:** Traefik listens on `websecure`, serving both `/vnc/{id}` (HTML) and `/websockify` (WebSocket). It strips the session prefix, applies TLS via `camofleet-tls`, and forwards traffic to the `camofleet-camo-fleet-worker-vnc` service on port 6900.【F:README.md†L152-L209】
3. **Gateway handling:** The FastAPI VNC gateway resolves the `{id}` to a backend host/port (`runner-vnc:590x`), enforces session limits, upgrades the connection, and begins relaying frames while sending periodic pings and enforcing idle/read timeouts.【F:worker-vnc/camofleet_worker_vnc/app.py†L100-L228】【F:worker-vnc/camofleet_worker_vnc/config.py†L25-L128】
4. **Runner bridge:** The runner launches an Xvfb display, starts `x11vnc` bound to the allocated TCP port, and returns the derived WebSocket URL so the UI connects through the gateway rather than directly to the raw RFB port.【F:runner/camoufox_runner/vnc.py†L120-L174】【F:runner/camoufox_runner/config.py†L24-L68】

## Observability & verification checklist

* **Access logs:** Capture Traefik access logs while opening the session; expect HTTP `101` responses on `/websockify` and long durations for sustained streams (see `artifacts/novnc/traefik-access.log`).【F:README.md†L152-L209】
* **Gateway logs:** Run the gateway with verbose logging to confirm identifier resolution, capacity enforcement, and upstream errors (see `artifacts/novnc/websockify.log`).【F:worker-vnc/camofleet_worker_vnc/app.py†L289-L322】
* **Packet capture:** Use `tcpdump -i any port 5900` within the gateway or runner container to capture the TCP handshake and RFB traffic (`artifacts/novnc/vnc.tcpdump.pcap`).【F:runner/camoufox_runner/vnc.py†L135-L155】
* **Manual validation:** In DevTools, ensure the WebSocket upgrade hits `/websockify?token={id}`, `Sec-WebSocket-Accept` matches the handshake, and frames continue for longer than one hour (increase the gateway idle/read timeouts if necessary).【F:worker-vnc/camofleet_worker_vnc/config.py†L25-L45】

## Artifact status

The repository ships placeholder log files because the execution environment for this report lacks Docker-in-Docker support. To regenerate them:

1. Run `docker compose up -d traefik vnc-gateway runner-vnc` in an environment with Docker.
2. Connect to `https://<host>/vnc/<id>` and interact with the session for several minutes.
3. Collect logs and packet captures as described above, then replace the placeholder files under `artifacts/novnc/`.

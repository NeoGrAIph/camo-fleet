# Camofleet k3s deployment

These manifests provide a minimal three-service deployment ready for a k3s cluster:

- `worker` — FastAPI service running the Playwright worker with VNC enabled.
- `control-plane` — lightweight orchestrator that proxies API requests to workers.
- `ui` — static React dashboard served by nginx.

## Prerequisites

1. Build and push the container images. Replace the registry references in the manifests.
   ```sh
   docker build -t REGISTRY/camofleet-worker:latest -f docker/Dockerfile.worker .
   docker build -t REGISTRY/camofleet-control:latest -f docker/Dockerfile.control .
   docker build -t REGISTRY/camofleet-ui:latest -f docker/Dockerfile.ui .
   docker push REGISTRY/camofleet-worker:latest
   docker push REGISTRY/camofleet-control:latest
   docker push REGISTRY/camofleet-ui:latest
   ```
2. Configure an Ingress controller in the cluster (e.g. Traefik, NGINX).
3. Provision a PersistentVolume if you need to keep session artefacts between restarts.

## Deploy

Update the image references inside `kustomization.yaml`, then apply:

```sh
kubectl apply -k deploy/k8s
```

This creates:

- `Deployment` + `Service` for each component.
- A `ConfigMap` with environment variables for the control-plane.
- An `Ingress` exposing the UI and API over HTTPS (edit hostnames before applying).
- A `Service` exposing the VNC WebSocket port (6900) as a ClusterIP.

## Environment variables

### Worker

- `WORKER_VNC_WS_BASE` — base URL of the worker VNC WebSocket (e.g. `ws://worker:6900`).
- `WORKER_VNC_HTTP_BASE` — base HTTP URL that the UI should use for the noVNC page.

### Control-plane

The control-plane reads workers from `CONTROL_WORKERS`, a JSON array of worker objects. The
manifest sets this automatically using the `CONTROL_WORKERS` environment variable.

Example value:

```json
[
  {
    "name": "worker-0",
    "url": "http://camofleet-worker:8080",
    "vnc_ws": "ws://camofleet-worker:6900",
    "vnc_http": "http://camofleet-worker:6900"
  }
]
```

### UI

By default the UI expects the control-plane at `/api`. Adjust the nginx configuration if you
expose it differently.

## TLS

The sample Ingress references a TLS secret named `camofleet-tls`. Provide your own certificate
or remove the TLS section for HTTP-only testing.

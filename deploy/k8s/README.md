# Camofleet k3s deployment

These manifests provide a minimal deployment ready for a k3s cluster:

- `worker` — API-под с sidecar runner'ом (Camoufox) в headless режиме.
- `worker-vnc` — такая же пара контейнеров, но runner собран с VNC/noVNC.
- `control-plane` — lightweight orchestrator that proxies API requests to workers.
- `ui` — static React dashboard served by nginx.

## Prerequisites

1. Build and push the container images. Replace the registry references in the manifests.
   ```sh
   docker build -t REGISTRY/camofleet-runner:latest -f docker/Dockerfile.runner .
   docker build -t REGISTRY/camofleet-runner-vnc:latest -f docker/Dockerfile.runner-vnc .
   docker build -t REGISTRY/camofleet-worker:latest -f docker/Dockerfile.worker .
   docker build -t REGISTRY/camofleet-control:latest -f docker/Dockerfile.control .
   docker build -t REGISTRY/camofleet-ui:latest -f docker/Dockerfile.ui .
   docker push REGISTRY/camofleet-runner:latest
   docker push REGISTRY/camofleet-runner-vnc:latest
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

This creates deployments for headless и VNC воркеров (каждый — пара контейнеров worker+runner),
контрольную плоскость, UI и Ingress. В `worker-vnc` диапазон переменных `RUNNER_VNC_PORT_*` и
`RUNNER_VNC_WS_PORT_*` зафиксирован на `5900` и `6900`, поэтому одновременно доступна только одна
VNC-сессия. Для внешнего доступа настройте TCP-проксирование этих портов (Ingress TCP/WS маршрут
или NodePort/LoadBalancer). `6900` нужен для noVNC/websockify, `5900` — для прямого VNC-клиента.

## Environment variables

### Worker + runner

- `WORKER_RUNNER_BASE_URL` — адрес sidecar runner'а (по умолчанию `http://localhost:8070`).
- `WORKER_SUPPORTS_VNC` — флаг, который сигнализирует control-plane, что воркер умеет в VNC.
- `RUNNER_VNC_WS_BASE` / `RUNNER_VNC_HTTP_BASE` — задаются только для runner-vnc и используются UI.
  Значения указываются без порта: runner автоматически добавит выделенный порт сессии.

### Control-plane

The control-plane reads workers from `CONTROL_WORKERS`, a JSON array of worker objects. The
manifest sets this automatically using the `CONTROL_WORKERS` environment variable.

Prometheus metrics are exposed on the path specified by `CONTROL_METRICS_ENDPOINT` (defaults to
`/metrics`). Configure your scraper to target the same Service/port.

Example value:

```json
[
  {
    "name": "worker-headless",
    "url": "http://camofleet-worker:8080",
    "supports_vnc": false
  },
  {
    "name": "worker-vnc",
    "url": "http://camofleet-worker-vnc:8080",
    "supports_vnc": true,
    "vnc_ws": "ws://camofleet-worker-vnc:{port}",
    "vnc_http": "http://camofleet-worker-vnc:{port}"
  }
]
```
Control-plane подставляет порт и хост активной сессии в плейсхолдер `{port}` (и `{host}`, если он
используется). Runner, в свою очередь, формирует исходные URL с фактическим портом из своего
диапазона, поэтому UI и API получают корректные публичные адреса без ручных правок.

### UI

By default the UI expects the control-plane at `/api`. Adjust the nginx configuration if you
expose it differently.

## TLS

The sample Ingress references a TLS secret named `camofleet-tls`. Provide your own certificate
or remove the TLS section for HTTP-only testing.

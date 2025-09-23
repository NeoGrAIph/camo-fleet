# Camofleet k3s deployment

These manifests provide a minimal deployment ready for a k3s cluster:

- `worker` — API-под с sidecar runner'ом (Camoufox) в headless режиме.
- `worker-vnc` — такая же пара контейнеров, но runner собран с VNC/noVNC.
- `control-plane` — lightweight orchestrator that proxies API requests to workers.
- `ui` — static React dashboard served by nginx.

## Prerequisites

1. Build the container images.
   ```sh
   docker build -t REGISTRY/camofleet-runner:latest -f docker/Dockerfile.runner .
   docker build -t REGISTRY/camofleet-runner-vnc:latest -f docker/Dockerfile.runner-vnc .
   docker build -t REGISTRY/camofleet-worker:latest -f docker/Dockerfile.worker .
   docker build -t REGISTRY/camofleet-control:latest -f docker/Dockerfile.control .
   docker build -t REGISTRY/camofleet-ui:latest -f docker/Dockerfile.ui .
   ```

   Push the images to a registry that is reachable from the cluster, or load them straight into
   the k3s containerd runtime if you cannot expose a registry.

   **Registry push**
   ```sh
   docker push REGISTRY/camofleet-runner:latest
   docker push REGISTRY/camofleet-runner-vnc:latest
   docker push REGISTRY/camofleet-worker:latest
   docker push REGISTRY/camofleet-control:latest
   docker push REGISTRY/camofleet-ui:latest
   ```

   **Directly into k3s** (requires root because k3s uses containerd)
   ```sh
   IMAGE_REF=camofleet-worker:latest            # tag that will appear in the manifests
   CONTAINERD_REF=docker.io/library/${IMAGE_REF} # adjust if you use a custom registry
   IMAGE_TAR=camofleet-worker.tar

   # optional: drop stale copies in containerd and the local Docker cache
   sudo ctr -n k8s.io images rm "${CONTAINERD_REF}" || true
   docker rmi "${IMAGE_REF}" || true

   docker build --no-cache -t "${IMAGE_REF}" -f docker/Dockerfile.worker .
   docker save "${IMAGE_REF}" -o "${IMAGE_TAR}"
   sudo ctr -n k8s.io images import "${IMAGE_TAR}"
   ```

   Repeat for every image (runner, runner-vnc, control, ui). Adjust `IMAGE_REF` and `IMAGE_TAR`
   per image or refactor into a small shell loop.
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
VNC-сессия. Поскольку слот всего один, манифесты дополнительно выключают прогрев VNC
(`RUNNER_PREWARM_VNC=0`): иначе раннер занял бы единственный дисплей ещё до первого запроса. Если
нужны прогретые VNC-сессии, расширьте диапазоны портов минимум до двух значений и поднимите
`RUNNER_PREWARM_VNC`.

Для внешнего доступа настройте TCP-проксирование этих портов (Ingress TCP/WS маршрут или
NodePort/LoadBalancer). `6900` нужен для noVNC/websockify, `5900` — для прямого VNC-клиента.

## Helm chart

Если предпочтительнее Helm, в каталоге [`deploy/helm`](../helm) есть пакетированный вариант тех же
манифестов. Он генерирует `CONTROL_WORKERS` автоматически на основе включённых воркеров. Пример
установки:

```sh
helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set global.imageRegistry=REGISTRY.example.com
```

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

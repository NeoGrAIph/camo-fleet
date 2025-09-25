# Camofleet Helm chart

This chart packages the manifests from [`deploy/k8s`](../k8s) so that the stack can be installed on a
k3s cluster with Helm.

## Configuration

Most of the values map directly to the original Kubernetes objects:

- `control`, `ui`, `worker`, `workerVnc` — container images, replica counts, probes and env vars.
- `global.imageRegistry` — optional registry prefix prepended to every image reference.
- `ui.controlHost` — optional hostname override for the UI nginx proxy when the control plane is
  reachable through a custom service or external domain.
- `workerVnc.vncPortRange` — диапазоны портов для `x11vnc` и идентификаторов VNC (`ws`). Raw-порты
  публикуются сервисом; гейтвей слушает единый порт `workerVnc.gatewayPort`.
- `workerVnc.controlOverrides` — публичные URL для VNC WebSocket и HTTP. Control-plane подставляет
  плейсхолдеры `{id}`/`{host}` при выдаче сессии.

By default the chart deploys both a headless and a VNC-capable worker. The control plane config map
is generated automatically from the enabled workers (the `values.yaml` keeps `control.config.workers`
set to `null` so Helm can inject the in-cluster service URLs). To expose VNC publicly configure
`workerVnc.controlOverrides` with your ingress URLs and create the Traefik routes manually (see the
repository `README.md` for a step-by-step example).

See `values.yaml` for all configurable options.

## Usage

```sh
# package images and push them to a registry that is reachable from the cluster
# (or load them directly into k3s as shown below)

helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set global.imageRegistry=myregistry.local
```

If the control plane runs behind a custom hostname, point the UI proxy at it with:

```sh
helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set ui.controlHost=control.example.com
```

The port still defaults to `control.service.port`, so update that value as well if the control plane
listens on a non-default port.

### Publishing the release with Traefik

The chart no longer provisions Traefik resources automatically. After installing the release, create
the ingress objects yourself so you can review every setting. The root [`README.md`](../../README.md)
contains a detailed guide with ready-to-adapt manifests for:

- an HTTP `IngressRoute` that maps `/` to the UI service and `/api` to the control service,
- a `Middleware` with `StripPrefixRegex` for `/vnc/{id}` URLs,
- IngressRoutes that map `/vnc/{id}` and `/websockify` to the gateway port (`6900`) exposed by the
  worker-vnc service,
- Helm overrides that feed the resulting public URLs into the control-plane configuration via
  `workerVnc.controlOverrides`.

Clusters without an ingress controller can still expose the UI and control plane through the
services that the chart creates. Switch the service type or rely on `kubectl port-forward` while you
experiment:

```sh
helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set ui.service.type=NodePort \
  --set control.service.type=NodePort
```

With NodePort services you can reach the UI through any node IP. For ad-hoc access you can keep the
default `ClusterIP` services and forward the ports instead:

```sh
kubectl port-forward svc/camofleet-ui 8080:80 -n camofleet
kubectl port-forward svc/camofleet-control 8900:9000 -n camofleet
```

### Loading images without an external registry

If the cluster cannot reach a registry, import the images into the k3s containerd runtime:

```sh
IMAGE_REF=camofleet-worker:latest            # tag referenced by the chart values
CONTAINERD_REF=docker.io/library/${IMAGE_REF} # change if you build with a custom registry prefix
IMAGE_TAR=camofleet-worker.tar

# optional: drop stale copies
sudo ctr -n k8s.io images rm "${CONTAINERD_REF}" || true
docker rmi "${IMAGE_REF}" || true

docker build --no-cache -t "${IMAGE_REF}" -f docker/Dockerfile.worker .
docker save "${IMAGE_REF}" -o "${IMAGE_TAR}"
sudo ctr -n k8s.io images import "${IMAGE_TAR}"
```

Repeat for the remaining images (runner, runner-vnc, control, ui) before running `helm upgrade --install`.

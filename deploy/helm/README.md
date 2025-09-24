# Camofleet Helm chart

This chart packages the manifests from [`deploy/k8s`](../k8s) so that the stack can be installed on a
k3s cluster with Helm.

## Configuration

Most of the values map directly to the original Kubernetes objects:

- `control`, `ui`, `worker`, `workerVnc` — container images, replica counts, probes and env vars.
- `global.imageRegistry` — optional registry prefix prepended to every image reference.
- `ui.controlHost` — optional hostname override for the UI nginx proxy when the control plane is
  reachable through a custom service or external domain.
- `ingress` — toggles Traefik `IngressRoute` generation for the UI and control plane. Provide a
  host, optional ingress class, entry points and TLS settings here.
- `workerVnc.ingressRoute` — Traefik resources for the VNC/noVNC ports. When enabled the chart can
  publish the entire port range under a path such as `/vnc/{port}` and automatically strip the
  prefix so that the noVNC assets remain accessible.
- `workerVnc.traefikService.enabled` — also create dedicated `TraefikService` objects per WebSocket
  port. Leave disabled if you prefer to reference the Kubernetes service directly from the
  generated IngressRoutes.

By default the chart deploys both a headless and a VNC-capable worker. The control plane config map
is generated automatically from the enabled workers (the `values.yaml` keeps `control.config.workers`
set to `null` so Helm can inject the in-cluster service URLs). When a public ingress is configured
the chart derives the default VNC URLs (WebSocket and HTTP) from the configured host, TLS settings
and path prefix, so the UI receives correct external endpoints without additional overrides.

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

### Exposing the release with Traefik

Enable the built-in Traefik resources when the cluster already ships the Traefik CRDs (k3s does by
default). Provide a host, optional ingress class and (if required) TLS configuration:

```sh
helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set ingress.enabled=true \
  --set ingress.host=camofleet.example.com \
  --set-string ingress.className=traefik \
  --set ingress.tls.secretName=camofleet-tls
```

To expose the VNC/noVNC ports under `/vnc/{port}` enable the dedicated IngressRoute. The chart will
create the `Middleware` needed to strip the prefix and will automatically propagate the resulting
public URLs to the control plane configuration:

```sh
helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set ingress.enabled=true \
  --set ingress.host=camofleet.example.com \
  --set workerVnc.ingressRoute.enabled=true
```

If you prefer referencing Traefik’s custom services instead of the Kubernetes service directly,
toggle `workerVnc.traefikService.enabled=true`. Additional middlewares can be attached through the
`ingress.middlewares`, `ingress.routes.*.middlewares` and `workerVnc.ingressRoute.middlewares`
lists. The default TLS scheme for the generated URLs is inferred from the `tls` sections under
`ingress` or `workerVnc.ingressRoute`.

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

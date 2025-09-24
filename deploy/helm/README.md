# Camofleet Helm chart

This chart packages the manifests from [`deploy/k8s`](../k8s) so that the stack can be installed on a
k3s cluster with Helm.

## Configuration

Most of the values map directly to the original Kubernetes objects:

- `control`, `ui`, `worker`, `workerVnc` — container images, replica counts, probes and env vars.
- `global.imageRegistry` — optional registry prefix prepended to every image reference.
- `ui.controlHost` — optional hostname override for the UI nginx proxy when the control plane is
  reachable through a custom service or external domain.

By default the chart deploys both a headless and a VNC-capable worker. The control plane config map
is generated automatically from the enabled workers, but you can override `control.config.workers`
with a custom array if you need to point the control plane at external nodes.

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

### Exposing the release

The Helm chart no longer manages ingress resources. Apply or maintain them separately so that they
match your cluster’s ingress controller and exposure requirements. The plain Kubernetes
manifest in [`deploy/k8s/ingress.yaml`](../k8s/ingress.yaml) can be reused as a starting point:

```sh
kubectl apply -n camofleet -f deploy/k8s/ingress.yaml
```

Before applying the manifest, update the host name, TLS secret and annotations so that they match
your environment. При необходимости ограничить доступ добавьте собственные middleware/правила в
Ingress или используйте отдельные Traefik CRDs, применяя их вручную.

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

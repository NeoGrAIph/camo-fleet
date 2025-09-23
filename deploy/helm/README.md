# Camofleet Helm chart

This chart packages the manifests from [`deploy/k8s`](../k8s) so that the stack can be installed on a
k3s cluster with Helm.

## Configuration

Most of the values map directly to the original Kubernetes objects:

- `control`, `ui`, `worker`, `workerVnc` — container images, replica counts, probes and env vars.
- `ingress` — host name, TLS secret and annotations for the HTTP entrypoint.
- `global.imageRegistry` — optional registry prefix prepended to every image reference.

By default the chart deploys both a headless and a VNC-capable worker. The control plane config map
is generated automatically from the enabled workers, but you can override `control.config.workers`
with a custom array if you need to point the control plane at external nodes.

See `values.yaml` for all configurable options.

## Usage

```sh
# package images and push them to a registry that is reachable from the cluster
# ...

helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set global.imageRegistry=myregistry.local \
  --set ingress.host=camofleet.local
```

The example assumes Traefik (the default k3s ingress controller). Adjust `ingress.className` and
TLS parameters to match your environment.

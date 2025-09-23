# Camofleet Helm chart

This chart packages the manifests from [`deploy/k8s`](../k8s) so that the stack can be installed on a
k3s cluster with Helm.

## Configuration

Most of the values map directly to the original Kubernetes objects:

- `control`, `ui`, `worker`, `workerVnc` — container images, replica counts, probes and env vars.
- `ingress` — host name, TLS secret and annotations for the HTTP entrypoint.
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
  --set global.imageRegistry=myregistry.local \
  --set ingress.host=camofleet.local
```

The example assumes Traefik (the default k3s ingress controller). Adjust `ingress.className` and
TLS parameters to match your environment.

If the control plane runs behind a custom hostname, point the UI proxy at it with:

```sh
helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set ui.controlHost=control.example.com
```

The port still defaults to `control.service.port`, so update that value as well if the control plane
listens on a non-default port.

### Deploying without an ingress controller

Clusters without an ingress controller can still expose the UI and control plane through the
services that the chart creates. Disable the built-in ingress manifest and either change the service
type or rely on `kubectl port-forward` while you experiment:

```sh
helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set ingress.enabled=false \
  --set ui.service.type=NodePort \
  --set control.service.type=NodePort
```

With NodePort services you can reach the UI through any node IP. For ad-hoc access you can keep the
default `ClusterIP` services and forward the ports instead:

```sh
kubectl port-forward svc/camofleet-ui 8080:80 -n camofleet
kubectl port-forward svc/camofleet-control 8900:9000 -n camofleet
```

### Traefik IngressRoute with Keycloak authentication

If you use Traefik’s CRDs and the Keycloak OpenID Connect plugin (for example to protect the UI at
`https://camofleet.services.synestra.tech`), disable the chart’s vanilla ingress resource and apply a
custom `Middleware` and `IngressRoute` instead. Install the release without the default ingress:

```sh
helm upgrade --install camofleet deploy/helm/camo-fleet \
  --namespace camofleet --create-namespace \
  --set ingress.enabled=false
```

The UI already proxies requests to the control service inside the release; override
`ui.controlHost` only if you expose the control plane through a different DNS name.

Then create Traefik resources similar to the following (replace the host, Keycloak realm, client
details and secret references with your own values):

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: camofleet-ui-kc-auth
  namespace: camofleet
spec:
  plugin:
    keycloakopenid:
      keycloakURL: "https://auth.synestra.io"
      keycloakRealm: "platform"
      clientID: "camofleet-ui"
      clientSecretFile: "/run/secrets/camofleet/keycloakClientSecret"
      scope: "openid profile email"
      userHeaderName: "X-User"
      userClaimName: "preferred_username"
      ignorePathPrefixes: "/api,/ws"
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: camofleet-ui
  namespace: camofleet
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`camofleet.services.synestra.tech`) && PathPrefix(`/`)
      kind: Rule
      middlewares:
        - name: camofleet-ui-kc-auth
      services:
        - name: camofleet-ui
          port: 80
  tls:
    certResolver: lehttp
```

Traefik will terminate TLS, enforce Keycloak authentication, and forward traffic to the UI service
within the cluster. Mount the client secret file into the Traefik pod as required by your
environment (for example via a Kubernetes secret volume).

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

# Traefik reverse-proxy extracts

The repository includes Kubernetes-ready Traefik custom resources that terminate TLS for both the UI and the noVNC gateway. These manifests illustrate how HTTPS/WSS is mapped to the internal `worker-vnc` service.

## noVNC HTTP + WebSocket routes

```yaml
apiVersion: traefik.io/v1alpha1
kind: Middleware
metadata:
  name: camofleet-worker-vnc-strip
  namespace: camofleet
spec:
  stripPrefixRegex:
    regex:
      - ^/vnc/[0-9]+
```
```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: camofleet-worker-vnc
  namespace: camofleet
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`camofleet.example.com`) && PathPrefix(`/vnc/`)
      kind: Rule
      middlewares:
        - name: camofleet-worker-vnc-strip
      services:
        - name: camofleet-camo-fleet-worker-vnc
          port: 6900
  tls:
    secretName: camofleet-tls
---
apiVersion: traefik.io/v1alpha1
kind: IngressRoute
metadata:
  name: camofleet-worker-vnc-websockify
  namespace: camofleet
spec:
  entryPoints:
    - websecure
  routes:
    - match: Host(`camofleet.example.com`) && PathPrefix(`/websockify`)
      kind: Rule
      services:
        - name: camofleet-camo-fleet-worker-vnc
          port: 6900
  tls:
    secretName: camofleet-tls
```

Source: `README.md` ("IngressRoute для VNC и WebSocket")【F:README.md†L152-L209】.

## Optional TCP passthrough

For raw TCP (e.g., tooling that needs direct access to the WS bridge port without HTTP routing), use an `IngressRouteTCP` bound to the dedicated entrypoint:

```yaml
apiVersion: traefik.io/v1alpha1
kind: IngressRouteTCP
metadata:
  name: camofleet-runner-vnc-6901
  namespace: camofleet
spec:
  entryPoints:
    - vnc-ws-6901
  routes:
    - match: HostSNI(`*`)
      services:
        - name: camofleet-worker-vnc
          port: 6901
```

Source: [`deploy/traefik/camofleet-runner-vnc-6901-irtcp.yaml`](../../deploy/traefik/camofleet-runner-vnc-6901-irtcp.yaml).【F:deploy/traefik/camofleet-runner-vnc-6901-irtcp.yaml†L1-L12】

## UI entrypoint

The UI itself is published via a separate `IngressRoute` that terminates TLS on the same `websecure` entrypoint:

```yaml
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
      services:
        - name: camofleet-camo-fleet-ui
          port: 80
  tls:
    certResolver: lehttp
```

Source: [`deploy/traefik/camofleet-ui-external-ir.yaml`](../../deploy/traefik/camofleet-ui-external-ir.yaml).【F:deploy/traefik/camofleet-ui-external-ir.yaml†L1-L16】

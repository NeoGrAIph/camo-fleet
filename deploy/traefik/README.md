# Publishing Camo Fleet through Traefik on k3s

k3s ships with Traefik installed by default. The manifest in this folder wires the Services created by the Helm chart to the public host `https://camofleet.services.synestra.tech/`.

## Before you apply the manifest

1. **Install the Helm release first.** Follow `deploy/helm/README.md` to deploy the workloads and Services in the `camofleet` namespace. Если вы переопределили `nameOverride` или `fullnameOverride`, отредактируйте `service.name` в манифесте так, чтобы он совпадал с реальными сервисами (например, `custom-control`).
2. **Make sure Traefik knows about TLS for the domain.** By default the manifest expects a certResolver named `letsencrypt`. Adjust the `tls` block in `camofleet-ingressroute.yaml` if your environment differs:
   - **Existing secret.** Replace the `tls` section with `tls: { secretName: camofleet-services-tls }` and create that secret in the `camofleet` namespace:
     ```bash
     kubectl create secret tls camofleet-services-tls \
       --namespace camofleet \
       --cert /path/to/fullchain.pem \
       --key /path/to/privkey.pem
     ```
   - **Different certResolver.** Change the `certResolver` value to match the name configured in Traefik (for example `lehttp`).
3. **Double-check DNS.** `camofleet.services.synestra.tech` must point to the public IP address of your k3s node or load balancer.

## Apply the IngressRoute

Once the prerequisites are satisfied, publish the services with:

```bash
kubectl apply -f deploy/traefik/camofleet-ingressroute.yaml
```

Verify that Traefik created the route:

```bash
kubectl get ingressroute -n camofleet camofleet
```

The `STATUS` column should read `True`. If you see `False`, describe the resource to view the error:

```bash
kubectl describe ingressroute -n camofleet camofleet
```

## What the manifest does

- `/` → `camofleet-ui:80`
- `/api` → `camofleet-control:9000`
- `/vnc` → `camofleet-worker-vnc:6080` (контейнер gateway внутри worker отвечает за noVNC)
- `/websockify` → проксирование на `camofleet-worker-vnc:6080` без дополнительного префикса, чтобы внешние noVNC WebSocket-URL выглядели как `https://camofleet.services.synestra.tech/websockify?token=...`

## Enable Keycloak ForwardAuth for the UI

The repository ships a first-step integration of the [ForwardAuth](https://doc.traefik.io/traefik/middlewares/http/forwardauth/) pattern described in `docs/security-options.md`. It protects only the UI route while we iterate on the API и noVNC paths.

1. **Provision secrets for oauth2-proxy.** Create a secret with your Keycloak client credentials, redirect URL и issuer:

   ```bash
   kubectl create secret generic oauth2-proxy \
     --namespace camofleet \
     --from-literal=client-id=<keycloak-client-id> \
     --from-literal=client-secret=<keycloak-client-secret> \
     --from-literal=cookie-secret=<random-32-byte-base64> \
     --from-literal=redirect-url=https://camofleet.services.synestra.tech/oauth2/callback \
     --from-literal=issuer-url=https://keycloak.example.com/realms/camofleet
   ```

   The redirect URL must match the value configured for the Keycloak client. `cookie-secret` needs to be a base64-encoded 32-byte string (for example `openssl rand -base64 32`).

2. **Deploy oauth2-proxy and the Traefik middleware:**

   ```bash
   kubectl apply -f deploy/traefik/oauth2-proxy.yaml
   kubectl apply -f deploy/traefik/camofleet-forward-auth.yaml
   ```

3. **Re-apply the IngressRoute.** The UI route already references the middleware; once the resources above exist, `/` will require a valid Keycloak session while `/api` и `/vnc` remain open for now:

   ```bash
   kubectl apply -f deploy/traefik/camofleet-ingressroute.yaml
   ```

To roll back the UI protection, delete the middleware and oauth2-proxy Deployment:

```bash
kubectl delete -f deploy/traefik/camofleet-forward-auth.yaml
kubectl delete -f deploy/traefik/oauth2-proxy.yaml
kubectl delete secret oauth2-proxy -n camofleet
```

## Remove the publication

To stop serving the application publicly, delete the IngressRoute (and the TLS secret if you created one):

```bash
kubectl delete -f deploy/traefik/camofleet-ingressroute.yaml
kubectl delete secret camofleet-services-tls -n camofleet  # optional
```

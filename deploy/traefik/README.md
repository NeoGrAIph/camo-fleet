# Publishing Camo Fleet through Traefik on k3s

k3s ships with Traefik installed by default. The manifest in this folder wires the Services created by the Helm chart to the public host `https://camofleet.services.synestra.tech/`.

## Before you apply the manifest

1. **Install the Helm release first.** Follow `deploy/helm/README.md` to deploy the workloads and Services in the `camofleet` namespace.
2. **Make sure Traefik knows about TLS for the domain.** You have two options:
   - **Use an existing certificate.** Create a TLS secret named `camofleet-services-tls` in the `camofleet` namespace:
     ```bash
     kubectl create secret tls camofleet-services-tls \
       --namespace camofleet \
       --cert /path/to/fullchain.pem \
       --key /path/to/privkey.pem
     ```
   - **Let Traefik issue certificates automatically.** If your Traefik installation already uses Let's Encrypt (for example via a certResolver called `letsencrypt`), edit `camofleet-ingressroute.yaml` and replace the `tls.secretName` block with:
     ```yaml
     tls:
       certResolver: letsencrypt
     ```
     Save the file after the change.
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
- `/vnc` → `camofleet-vnc-gateway:6080` (supports both HTTPS and WebSocket connections)

## Remove the publication

To stop serving the application publicly, delete the IngressRoute (and the TLS secret if you created one):

```bash
kubectl delete -f deploy/traefik/camofleet-ingressroute.yaml
kubectl delete secret camofleet-services-tls -n camofleet  # optional
```

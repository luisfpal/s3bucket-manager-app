# Deploying to Production

## Why the manifests look the way they do

`k8s/manifests/` is divided into `app/` and `infra/` because in production these two layers have different owners.

At AREA Science Park, Authentik is a shared identity platform administered by the infrastructure team — not by the team operating Bucket Explorer. The `infra/` directory contains Authentik and its dependencies (PostgreSQL, Redis). The `app/` directory contains the webapp: Django backend, React frontend, PostgreSQL. The boundary between them is intentional and reflects a real organizational split.

The development environment reproduces this boundary explicitly so that deploying to production is a **subtraction problem**: you hand off `infra/` responsibility to whoever administers Authentik, and apply only `app/` yourself. The scripts and structure you use during development are close enough to production that there are no surprises at deploy time.

For the full development topology (virtual machines, IP layout, networking), see [dev-environment-overview.md](dev-environment-overview.md).

---

## Before you start

You need:

- A Kubernetes cluster where you have permission to create the `bucket-explorer` namespace
- An Authentik instance already running, **or** willingness to deploy one (see [Scenario B](#scenario-b--no-existing-authentik))
- OIDC client credentials from the Authentik administrator
- Ceph RGW endpoint URL and RGWSquared service credentials
- A container registry reachable by your cluster nodes (see [Container image ownership](#container-image-ownership))

---

## Scenario A — Authentik is already deployed

This is the normal case at AREA Science Park. The Authentik administrator creates an OAuth2 provider for Bucket Explorer and gives you:

| Credential | Used as |
|---|---|
| Client ID | `OIDC_CLIENT_ID` in the ConfigMap |
| Client secret | `oidc-client-secret` in the Secret |
| Application slug | `OIDC_APPLICATION_SLUG` in the ConfigMap |
| Internal Authentik service URL | `AUTHENTIK_URL` in the ConfigMap |
| Public Authentik URL | `AUTHENTIK_EXTERNAL_URL` in the ConfigMap |

The redirect URI to register in Authentik is: `https://<your-domain>/api/oauth/complete/authentik/`

### 1. Prepare configuration

Use `k8s/env/dev/backend-config.yaml` as a template for your production ConfigMap. The values that must change:

| Key | Dev default | Production value |
|---|---|---|
| `DJANGO_DEBUG` | `True` | `False` |
| `DJANGO_ALLOWED_HOSTS` | `*` | Your domain(s), comma-separated |
| `AUTHENTIK_URL` | In-cluster dev URL | Internal Authentik service URL |
| `AUTHENTIK_EXTERNAL_URL` | `http://localhost:9000` | `https://<authentik-public-domain>` |
| `OIDC_CLIENT_ID` | placeholder | From Authentik admin |
| `OIDC_APPLICATION_SLUG` | placeholder | From Authentik admin |
| `S3_ENDPOINT` | placeholder | `https://<ceph-rgw-endpoint>` |
| `S3_VERIFY_SSL` | `False` | `True` (unless using a self-signed cert) |
| `RGWSQUARED_URL` | placeholder | `https://<rgwsquared-endpoint>` |
| `OAUTH_LOG_LEVEL` | `DEBUG` | `INFO` |

Use `k8s/env/dev/app-secrets.yaml` as a template for your production Secret. Generate real values — never reuse the dev placeholders:

| Key | How to generate |
|---|---|
| `django-secret-key` | `python -c "import secrets; print(secrets.token_urlsafe(50))"` |
| `database-password` | `openssl rand -base64 32` |
| `oidc-client-secret` | From the Authentik admin |
| `rgwsquared-username` | From the RGWSquared admin |
| `rgwsquared-password` | From the RGWSquared admin |
| `django-superuser-password` | Choose a strong initial password; change it immediately after first login |

Store your production files outside the repository. `k8s/env/dev/*.local.yaml` is gitignored for exactly this reason — use the same pattern locally.

### 2. Apply the manifests

Apply in order. Each step waits for its dependency before the next one starts.

```bash
# Namespace
kubectl apply -f k8s/manifests/app/00-namespace.yaml

# Configuration (your production versions, not the dev templates)
kubectl apply -f <your-production-secrets.yaml>
kubectl apply -f <your-production-configmap.yaml>

# PostgreSQL — must be ready before the backend starts
kubectl apply -f k8s/manifests/app/01-django-postgres.yaml
kubectl rollout status deployment/django-postgres -n bucket-explorer

# Backend (Django + gunicorn)
kubectl apply -f k8s/manifests/app/02-backend.yaml
kubectl rollout status deployment/backend -n bucket-explorer

# Frontend (nginx + React SPA)
kubectl apply -f k8s/manifests/app/03-frontend.yaml
kubectl rollout status deployment/frontend -n bucket-explorer
```

Backend startup runs Django migrations, loads UO mapping fixtures, creates the superuser, and collects static files automatically. Watch the logs during the first deploy to confirm each step completes cleanly before gunicorn starts:

```bash
kubectl logs -n bucket-explorer deployment/backend -f
```

Expected sequence: `migrate` → `load_uo_mappings` → `ensure_superuser` → `collectstatic` → gunicorn listening on port 8000.

### 3. Configure Ingress

`k8s/manifests/app/04-ingress.yaml` is a reference template. Update the `ingressClassName` and `host` fields to match your cluster's routing setup. The template uses the standard Kubernetes `IngressClass` mechanism; your infrastructure team will know which class to use.

If your cluster routes differently (NodePort, LoadBalancer, or a custom HAProxy configuration), adapt accordingly. The services you need to expose:

| Service | Port | What it serves |
|---|---|---|
| `frontend-service` | 80 | React SPA + proxied `/api/*` calls |
| `backend-service` | 8000 | Django REST API (internal; reached via frontend nginx) |

Only `frontend-service` needs to be externally accessible. The backend is proxied by nginx inside the pod.

### 4. Verify

| Check | Expected result |
|---|---|
| `curl https://<domain>/api/health/` | `{"status": "ok"}` |
| `https://<domain>/admin/login` | Admin panel loads; log in with superuser credentials |
| Click "Login with Authentik" | OIDC redirect to Authentik, JWT returned after login |
| Activate a tenant in admin panel | Buckets and users sync from RGWSquared |

---

## Scenario B — No existing Authentik

If deploying a fully standalone instance, apply `k8s/manifests/infra/` first to start Authentik and its dependencies, then follow Scenario A for the webapp. The development script `k8s/infra.sh deploy` automates this for the dev topology; for production, adapt the Ingress and DNS setup to your environment.

After Authentik is running:

1. Log in to the Authentik admin UI and create an OAuth2 provider for Bucket Explorer
2. Note the client ID, client secret, and application slug
3. Register the redirect URI: `https://<domain>/api/oauth/complete/authentik/`
4. Proceed with Scenario A

---

## Container image ownership

The manifests reference images at `ghcr.io/luisfpal/buckets-explorer-{backend,frontend}:latest`. These live in the personal container registry of the original maintainer. A team deploying Bucket Explorer on their own infrastructure should publish their own images.

To take ownership:

1. Update `GHCR_OWNER` near the top of `k8s/app.sh` to your GitHub username or organisation
2. Create `k8s/.env` (gitignored — you must create it locally):
   ```
   GHCR_TOKEN=<GitHub classic PAT with write:packages scope>
   ```
3. Update the `image:` fields in `k8s/manifests/app/02-backend.yaml` and `k8s/manifests/app/03-frontend.yaml` to point at your registry
4. Build and push:
   ```bash
   cd k8s && ./app.sh deploy --rebuild
   ```

For production deployments, tag images with specific versions instead of `:latest` to ensure reproducible rollouts. The `:latest` tag is convenient during development but makes it impossible to know exactly which build is running in production.

`k8s/.env` is gitignored and must be created by each maintainer who needs to push images. Team members who only work with the running application do not need it.

---

## Resource sizing

The manifest resource requests are calibrated for the development environment. Production sizing depends on your workload and available cluster capacity:

| Component | Dev requests / limits | Starting point for production |
|---|---|---|
| Backend | 250m / 1000m CPU · 512Mi / 1Gi RAM | Scale gunicorn workers (`--workers N`) with available CPU; each worker needs ~200 MB |
| PostgreSQL | 250m / 1000m CPU · 512Mi / 1Gi RAM | Scale with active tenant count and query volume |
| Frontend (nginx) | 50m / 100m CPU · 64Mi / 128Mi RAM | Static file server; rarely the bottleneck |

Edit the `resources:` blocks in `k8s/manifests/app/02-backend.yaml` and `k8s/manifests/app/01-django-postgres.yaml` before applying in production. The `02-backend.yaml` manifest includes commented production sizing guidance inline.

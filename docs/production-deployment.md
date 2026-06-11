# Deploying to Production

## Why the manifests look the way they do

`k8s/manifests/` is divided into `app/` and `infra/` because in production these two layers have different owners.

At AREA Science Park, Authentik is a shared identity platform administered by the infrastructure team — not by the team operating Buckets Explorer. The `infra/` directory contains Authentik and its dependencies (PostgreSQL, Redis). The `app/` directory contains the webapp: Django backend, React frontend, PostgreSQL. The boundary between them is intentional and reflects a real organizational split.

The development environment reproduces this boundary explicitly so that deploying to production is a **subtraction problem**: you hand off `infra/` responsibility to whoever administers Authentik, and apply only `app/` yourself. The scripts and structure you use during development are close enough to production that there are no surprises at deploy time.

For the full development topology (virtual machines, IP layout, networking), see [dev-environment-overview.md](dev-environment-overview.md).

---

## Before you start

You need:

- A Kubernetes cluster where you have permission to create the `bucket-explorer` namespace
- A **kubeconfig** for that cluster on the machine where you run `kubectl` and apply manifests (see [Kubernetes access (kubeconfig)](#kubernetes-access-kubeconfig))
- An Authentik instance already running, **or** willingness to deploy one (see [Scenario B](#scenario-b--no-existing-authentik))
- OIDC client credentials from the Authentik administrator
- Ceph RGW endpoint URL and RGWSquared service credentials
- A container registry reachable by your cluster nodes (see [Container image ownership](#container-image-ownership))

---

## Kubernetes access (kubeconfig)

Production operators need a kubeconfig file issued by the platform or cluster team. It is the same concept as in development: a file that tells `kubectl` **where** the API server is and **how** to authenticate.

Typical setup on the operator workstation:

```bash
# Path chosen by your platform team — store outside Git, mode 600
export KUBECONFIG=/absolute/path/to/prod-kubeconfig.yaml

kubectl config current-context
kubectl get ns bucket-explorer
kubectl auth can-i create deployments -n bucket-explorer
```

All three commands must succeed before you apply manifests or roll out image updates.

| Check | Pass criteria |
|-------|----------------|
| `kubectl config current-context` | Points at the intended production cluster (not a dev context) |
| `kubectl get ns bucket-explorer` | Namespace exists or you have permission to create it |
| `kubectl auth can-i create deployments -n bucket-explorer` | `yes` |

**Network access:** Production API servers are usually reachable only from an institutional VPN or bastion — the same role the dev SSH tunnel plays in [dev-environment-setup.md](dev-environment-setup.md#step-8-k8s-api-access-tunnel), but with your organization's production networking instead of `localhost:16443`.

**Session habit:** Export `KUBECONFIG` in every shell (or merge the production context into `~/.kube/config` with `kubectl config use-context`). Deployment scripts and CI jobs must set `KUBECONFIG` explicitly when they are not using the default kubeconfig path.

**Security:** Never commit kubeconfig files. Treat them like passwords (file mode `600`, store in a secrets manager or secure home directory).

---

## Scenario A — Authentik is already deployed

This is the normal case at AREA Science Park. The Authentik administrator creates an OAuth2 provider for Buckets Explorer and gives you:

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

Store your production files outside the repository. `k8s/env/dev/*.local.yaml` is gitignored for exactly this reason — use the same pattern locally.

### 2. Apply the manifests

Production operators with access only to the `bucket-explorer` namespace apply **`k8s/manifests/app/`** and their own ConfigMap/Secret overlays. Do **not** apply `k8s/manifests/infra/` or `k8s/env/prod/infra-secrets.yaml` — Authentik is external and administered outside this repository.

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

Set `AUTHENTIK_ADMIN_GROUP` in your production ConfigMap (default: `buckets-explorer-admin`) and assign admin users to that group in Authentik.

Backend startup runs Django migrations, loads UO mapping fixtures, purges legacy local staff users, and collects static files automatically. Watch the logs during the first deploy to confirm each step completes cleanly before gunicorn starts:

```bash
kubectl logs -n bucket-explorer deployment/backend -f
```

Expected sequence: `migrate` → `load_uo_mappings` → `purge_local_staff_users` → `collectstatic` → gunicorn listening on port 8000.

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
| `https://<domain>/admin/login` | Admin panel loads; log in with Authentik (admin group member) |
| Click "Login with Authentik" | OIDC redirect to Authentik, JWT returned after login |
| Activate a tenant in admin panel | Buckets and users sync from RGWSquared |

---

## Scenario B — No existing Authentik

If deploying a fully standalone instance, apply `k8s/manifests/infra/` first to start Authentik and its dependencies, then follow Scenario A for the webapp. The development script `k8s/infra.sh deploy` automates this for the dev topology; for production, adapt the Ingress and DNS setup to your environment.

After Authentik is running:

1. Log in to the Authentik admin UI and create an OAuth2 provider for Buckets Explorer
2. Note the client ID, client secret, and application slug
3. Register the redirect URI: `https://<domain>/api/oauth/complete/authentik/`
4. Proceed with Scenario A

---

## Container image ownership

The manifests reference images at `ghcr.io/luisfpal/buckets-explorer-{backend,frontend}:latest`. That registry was used during development for full control and rapid iteration. A team deploying Buckets Explorer on their own infrastructure must publish their own images and update the manifests accordingly.

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

---

## Production mental model

You typically own only the `bucket-explorer` namespace. Cluster admins own Authentik, DNS, TLS, and public routing.

| Service | Type | Purpose |
| --- | --- | --- |
| `frontend-service` | ClusterIP | Public traffic should route here (port 80) |
| `backend-service` | ClusterIP | Internal API; reached via frontend nginx |
| `django-postgres` | ClusterIP | Application PostgreSQL |

Storage boundaries: Django holds metadata; RGWSquared owns bucket policy; Ceph RGW stores objects.

**Golden rule:** every `kubectl apply` is followed by a verification check. If a check fails, stop and fix before continuing.

Run `./k8s/app.sh verify` (or confirm CI is green) **before** building images for production.

---

## Prepare production configuration

Create `k8s/env/prod/app-secrets.local.yaml` and `k8s/env/prod/backend-config.local.yaml` outside Git (mode `600`). Use `k8s/env/prod/*.yaml` templates as starting points.

### Expected secret keys

```text
database-password
django-secret-key
oidc-client-secret
rgwsquared-password
rgwsquared-username
```

Admin access uses **Authentik** (`AUTHENTIK_ADMIN_GROUP` in the ConfigMap, default `buckets-explorer-admin`). There is no local Django admin password secret.

### Preflight checks

```bash
export KUBECONFIG=/absolute/path/to/prod-kubeconfig.yaml
export NS=bucket-explorer

kubectl config current-context
kubectl get ns "$NS"
kubectl auth can-i create deployments -n "$NS"

chmod 600 k8s/env/prod/app-secrets.local.yaml k8s/env/prod/backend-config.local.yaml

# No unfilled placeholders
grep -q "REPLACE_WITH_PROD" k8s/env/prod/backend-config.local.yaml && echo "FAIL" || echo "OK"

kubectl apply --dry-run=client --validate=false -f k8s/env/prod/app-secrets.local.yaml
kubectl apply --dry-run=client --validate=false -f k8s/env/prod/backend-config.local.yaml
```

---

## Updating after code changes

Changing source files does not affect running pods. You must **build a new image → push → restart** the deployment.

### Decision table

| Change | Rebuild image? | Action |
| --- | --- | --- |
| Python/Django code, migrations | Backend | Build backend → push → `kubectl rollout restart deployment/backend` |
| React/TypeScript or `nginx.conf` | Frontend | Build frontend → push → restart frontend |
| Both | Both | Build and restart both |
| ConfigMap env vars only | No | `kubectl apply` ConfigMap → restart backend |
| Secret values (except DB password) | No | `kubectl apply` Secret → restart backend |
| `database-password` | No | Change live PostgreSQL role password, apply Secret, restart (see below) |

`imagePullPolicy: Always` on deployments ensures `rollout restart` pulls the latest `:latest` digest from the registry.

### Build and push (example)

```bash
export NS=bucket-explorer

# Authenticate to ghcr.io (PAT needs write:packages)
gh auth token | podman login ghcr.io -u <owner> --password-stdin

podman build -t ghcr.io/<owner>/buckets-explorer-backend:latest \
  -f backend/Containerfile backend/
podman push ghcr.io/<owner>/buckets-explorer-backend:latest

kubectl rollout restart deployment/backend -n "$NS"
kubectl rollout status deployment/backend -n "$NS" --timeout=300s
kubectl logs -n "$NS" deployment/backend --tail=50
```

Expected backend logs: `migrate` → `load_uo_mappings` → `purge_local_staff_users` → `collectstatic` → gunicorn.

Frontend build compiles React inside the container (no local Node.js required):

```bash
podman build -t ghcr.io/<owner>/buckets-explorer-frontend:latest \
  -f frontend/Containerfile frontend/
podman push ghcr.io/<owner>/buckets-explorer-frontend:latest
kubectl rollout restart deployment/frontend -n "$NS"
```

### Post-update verification

```bash
export APP_HOST=<production-domain>

curl -fsS  "https://$APP_HOST/api/health/"
curl -fsSI "https://$APP_HOST/api/oauth/login/authentik/" | grep -i location
```

- Health returns `{"status":"ok"}`.
- OIDC `Location` header should show `redirect_uri=https://` (not `http://`).
- Admin Panel: open `/admin/login`, sign in with Authentik (member of admin group).

---

## Routine updates (Class A and B)

Most production changes are safe rollouts that keep the PostgreSQL PVC intact.

| Class | Examples | Data risk |
| --- | --- | --- |
| **A** | ConfigMap/Secret change, OIDC rotation | None — restart backend after apply |
| **B** | New backend/frontend image, migration-only schema change | Low — PVC survives; run post-deploy health checks |

After any Class A or B update:

1. Confirm pods are `Running` and readiness probes pass.
2. Hit `/api/health/` on the public URL.
3. Spot-check user login and Admin Panel sync.

See [storage-cache-and-redeploy.md](storage-cache-and-redeploy.md) for the three-layer model.

---

## After database loss (Class C)

Deleting the PostgreSQL PVC (`app.sh cleanup` in dev, or an intentional prod reset) wipes Django metadata only. Ceph RGW and RGWSquared keep existing buckets and policies.

After redeploy:

1. Run **Admin Panel → Sync → Refresh local cache** for each tenant.
2. Review buckets flagged **ORPHAN** in the admin Buckets view — these exist in RGW but are **not** visible on user dashboards.
3. Delete orphans that should not exist (admin **Delete** calls RGWSquared `bucketDelete`).
4. Tell researchers to recreate needed buckets through the webapp — sync does not restore user access for manual RGW buckets.
5. Do **not** expect Django to auto-delete orphan Ceph buckets without an explicit admin delete.

Full semantics: [storage-cache-and-redeploy.md](storage-cache-and-redeploy.md).

---

## Updating secrets and configuration

Kubernetes injects Secret/ConfigMap values when a pod **starts**. After `kubectl apply`, restart the backend:

```bash
kubectl apply -f k8s/env/prod/app-secrets.local.yaml
kubectl apply -f k8s/env/prod/backend-config.local.yaml
kubectl rollout restart deployment/backend -n bucket-explorer
kubectl rollout status deployment/backend -n bucket-explorer --timeout=300s
```

| Change | Notes |
| --- | --- |
| `django-secret-key` | Invalidates existing JWTs/sessions; plan a maintenance window |
| `oidc-client-secret` | Verify OIDC login after rollout |
| `rgwsquared-*` | Verify tenant sync in admin panel after rollout |
| `database-password` | Update PostgreSQL role password first, then apply Secret and restart. Do not delete the PVC for routine rotation |

Default to **preserving PostgreSQL data**. Only recreate the database PVC for an intentional fresh install with explicit data-loss approval.

---

## Database migrations

The backend pod CMD runs `python manage.py migrate --noinput` before gunicorn starts. The readiness probe blocks traffic until gunicorn listens.

- **Additive migrations** (new tables/columns): rolling restart is usually safe.
- **Destructive migrations** (drop/rename): use a two-phase deploy—deploy code that no longer uses the old schema, then deploy the migration.

---

## Troubleshooting (quick reference)

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Backend CrashLoopBackOff after secret change | Invalid config or migration error | `kubectl logs deployment/backend --previous` |
| Public URL 404 but port-forward works | Routing/DNS/TLS | Ask admins to route domain to `frontend-service:80` |
| OIDC `redirect_uri=http://` | `AUTHENTIK_EXTERNAL_URL` wrong | Fix ConfigMap, restart backend |
| Admin login 403 | User not in `AUTHENTIK_ADMIN_GROUP` | Add user to Authentik group; check ConfigMap value |
| RGWSquared sync fails | Wrong credentials or URL | Check Secret and `RGWSQUARED_URL` in ConfigMap |

---

## Final production checklist

- [ ] `KUBECONFIG` points to production cluster
- [ ] `app-secrets.local.yaml` and `backend-config.local.yaml` exist, mode `600`, not committed
- [ ] No `REPLACE_WITH_PROD` placeholders remain
- [ ] `AUTHENTIK_ADMIN_GROUP` set; admin users assigned in Authentik
- [ ] PostgreSQL, backend, frontend pods `1/1 Running`
- [ ] `/api/health/` OK via public URL
- [ ] User OIDC login and Admin Panel Authentik login work
- [ ] RGWSquared sync and a disposable test-tenant smoke test pass

---

## Automated production deploy (future)

Production clusters often cannot host self-hosted GitHub runners. The recommended path when automation is needed: GitHub managed runner + scoped kubeconfig secret + manual approval gate on `main`. Dev automation is documented in [testing-and-ci.md](testing-and-ci.md).

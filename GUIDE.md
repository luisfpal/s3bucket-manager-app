# S3 Bucket Manager — Architecture Guide

This guide explains **why** things work the way they do, not just **what** they are.

## The Big Picture: What Problem Are We Solving?

```
┌─────────────────────────────────────────────────────────────────────┐
│  K3s Cluster                         │    Ceph Cluster             │
│                                       │                             │
│  React ──► nginx ──► Django ──────────┼──► RGW (S3 API)            │
│                       │               │        │                    │
│                     PostgreSQL        │     3× OSD (replicated)     │
│                     Authentik         │     data survives failures  │
└───────────────────────────────────────┴─────────────────────────────┘
RGWSquared provides tenant/user/bucket metadata. Ceph RGW stores object data.
```

**The key insight:** Django talks to object storage through the **S3 protocol**. Ceph RGW can therefore sit outside the application namespace while Django still uses standard `boto3` calls.

## Infrastructure Context

Development and validation happened in a Stencil virtual datacenter: a virtualized multi-node environment that runs real K3s, Ceph RGW S3-compatible storage, and supporting identity/networking services. Stencil matters because the app was tested against production-like boundaries: images are built and pushed to a registry, K3s pulls them onto cluster nodes, Authentik handles OAuth2/OIDC, and object data lives in Ceph rather than inside the app pod.

This guide keeps concrete hostnames, IP addresses, and secrets out of the public path. Use `k8s/env/<env>/` templates and ignored `*.local.yaml` files for environment-specific values.

---

## Network Topology: How Traffic Flows

Understanding the network is the foundation. Every deployment has the same logical chain even if hostnames and tunnels differ.

### Logical Network

```
WORKSTATION
    │
    │ optional SSH local forwards
    ▼
DEPLOYMENT HOST
    │
    │ kubectl port-forward / kubeconfig
    ▼
K3S CLUSTER
    ├── frontend-service → nginx + React
    ├── backend-service  → Django REST API
    ├── PostgreSQL
    └── Authentik
         │
         ├── RGWSquared API
         └── Ceph RGW S3 endpoint
```

### SSH Tunneling: Why and How

**Problem:** Your browser may run on a workstation that cannot directly reach cluster services.

**Solution:** Use local forwards to the deployment host, then `kubectl port-forward` from that host into the cluster:

```
WORKSTATION                    DEPLOYMENT HOST              K3S POD
───────────                    ───────────────              ───────

Browser ◄── localhost:3000 ──── SSH tunnel ──── localhost:3000 ──── kubectl port-forward ──── frontend:80
```

**What happens when you open `localhost:3000` in your browser:**

1. Your browser sends a request to `localhost:3000`
2. SSH forwards it to the deployment host if the cluster is remote
3. `kubectl port-forward` forwards it to the frontend pod's port 80
4. nginx inside the pod serves React HTML/JS
5. For API calls: nginx proxies to Django via K8s service DNS
6. Response flows back the same chain

Two separate mechanisms are involved:
- **SSH tunnel** bridges workstation and deployment host
- **kubectl port-forward** bridges deployment host and K3s pod

Both are needed when there is no direct route from the workstation to K3s services.

---

## Container Images: From Source to K3s

### The Problem

K3s uses containerd on each node. Nodes pull images from the configured container registry. The deployment problem is therefore ensuring image build/push succeeds and nodes can pull from that registry.

### The Solution

```
  BUILD (deployment host)           PUSH                           PULL (on K3s nodes)
  ─────────────                    ──────                         ───────────────────

  podman build ──► podman push ──► configured registry ──► kubelet/containerd pull
     │
  Why podman?
  - Rootless (no daemon)
  - OCI-compatible (same image format as Docker)
```

### Why `imagePullPolicy: IfNotPresent`?

```yaml
containers:
- name: backend
  image: <registry>/s3mgr-backend:latest
  imagePullPolicy: IfNotPresent
```

With `IfNotPresent`, each node pulls when needed and reuses local cache otherwise. Deployment updates happen by building/pushing a new image and restarting the deployment.

### Operator workflow

The maintained operator path is `k8s/dev.sh`. It handles image build/push, environment overlays, Authentik configuration, access setup, health checks, and cleanup from one script. Use this guide to understand the moving parts, then use the script for day-to-day operation.

---

## S3 Protocol: Why the Migration Was Almost Free

### The S3 Protocol Layer

```
     APPLICATION CODE                S3 PROTOCOL              STORAGE BACKEND
     ──────────────                 ────────────             ────────────────

     boto3.client('s3')    ──► HTTP PUT/GET/DELETE ──►   ┌── Ceph RGW  ← this deployment
                                   (REST API)             ├── AWS S3
                                                          └── any S3-compatible endpoint
```

**What `boto3` actually does:** sends HTTP requests. That's it.

```python
# Creating a bucket is just an HTTP PUT:
# PUT /my-bucket HTTP/1.1
# Host: <s3-rgw-endpoint>
# Authorization: AWS4-HMAC-SHA256 Credential=...

s3.create_bucket(Bucket="my-bucket")
```

The same client code works against any S3-compatible endpoint. The deployment-specific values are endpoint URL, credentials, and TLS policy.

### The Configuration Change

```python
boto3.client('s3',
    endpoint_url='https://<s3-rgw-endpoint>',
    aws_access_key_id='<ceph-access-key>',
    aws_secret_access_key='<ceph-secret-key>',
    verify='<true-or-ca-bundle-path>',
)
```

The S3 boundary is intentionally configuration-driven. Application code should not hard-code a storage backend.

### Self-Signed Certificates

```
                              RGW ingress / load balancer
                                      │
                      ┌───────────────┼───────────────┐
                      │               │               │
                  ceph-svc01      ceph-svc02      ceph-svc03
                  (RGW daemon)   (RGW daemon)    (RGW daemon)
```

Development RGW deployments may use a private CA or self-signed certificate. Production should trust the CA bundle and keep `S3_VERIFY_SSL=True`.

---

## Canonical K3s Deployment Shape

### Deployment decisions

| Decision | Why it matters |
|----------|----------------|
| Registry push/pull (`podman push` + K3s pull) | Every K3s node can fetch the same image without local image import tricks. |
| K8s service DNS | nginx and Django address services by stable cluster names, not pod IPs. |
| External Ceph RGW | Object data lives in the storage platform, not inside the application namespace. |
| Authentik in-cluster | OAuth2/OIDC login is deployed with the app and configured by `configure_authentik.py`. |
| MIME types ConfigMap for Authentik | Authentik serves static assets consistently in this K3s deployment. |

### Stable pieces

| Piece | Role |
|-------|------|
| Namespace `storage-system` | Keeps all app resources in one Kubernetes boundary. |
| Authentik setup | Provides the OAuth2/OIDC identity boundary. |
| Django app structure | Holds auth, tenant, sync, and S3 business logic. |
| React frontend | Uses the API and stays storage-backend agnostic. |
| `configure_authentik.py` | Creates or updates the Authentik provider/client after deploy. |

### nginx.conf: From Hack to Clean

```
BEFORE (Minikube — broken DNS):
  upstream backend {
      server BACKEND_HOST:8000;     ← placeholder replaced at deploy time
  }                                    with the pod's actual IP address
                                       (because DNS didn't work)

AFTER (K3s — DNS works):
  upstream backend {
      server backend-service.storage-system.svc.cluster.local:8000;
  }                                 ← standard K8s service DNS
                                       CoreDNS resolves this to the
                                       backend pod's ClusterIP
```

---

## Quick Reference

### Common kubectl Commands

```bash
# Set kubeconfig (MUST do this first — uses SSH tunnel on port 16443)
export KUBECONFIG=/tmp/k3s-tunnel-kubeconfig.yaml

# View all resources
kubectl get all -n storage-system

# Check pod logs
kubectl logs -l app=backend -n storage-system --tail=50
kubectl logs -l app=frontend -n storage-system --tail=50

# Debug a failing pod
kubectl describe pod <pod-name> -n storage-system
kubectl logs <pod-name> -n storage-system --previous

# Interactive shell in backend pod
kubectl exec -it deployment/backend -n storage-system -- /bin/bash

# Test S3 from inside the backend pod
# NOTE: Must set DJANGO_SETTINGS_MODULE because we're running outside gunicorn
kubectl exec -it deployment/backend -n storage-system -- python -c "
import os; os.environ['DJANGO_SETTINGS_MODULE'] = 'settings'
import django; django.setup()
from storage.models import Tenant
from storage.services.s3_ops import get_mgmt_s3_client
tenant = Tenant.objects.get(code='NFFADI')
s3 = get_mgmt_s3_client(tenant)
print(s3.list_buckets())
"
```

### Port-Forwarding Quick Reference

```bash
# ON THE DEPLOYMENT HOST — set kubeconfig and start port-forwards:
export KUBECONFIG=/tmp/k3s-tunnel-kubeconfig.yaml
nohup kubectl port-forward -n storage-system svc/frontend-service 3000:80 > /tmp/pf-frontend.log 2>&1 &
nohup kubectl port-forward -n storage-system svc/authentik-service 9000:9000 > /tmp/pf-authentik.log 2>&1 &

# ON YOUR WORKSTATION — SSH tunnel if the deployment host is remote:
ssh -L 3000:localhost:3000 -L 9000:localhost:9000 <deployment-host>

# Then open: http://localhost:3000
```

> **After any reboot**, run `cd k8s && ./dev.sh check` before debugging the application layer.

### Important Gotchas

| Gotcha | Details |
|--------|---------|
| K3s service name | `k3s-server` (NOT `k3s`). Installed by Ansible role. |
| Kubeconfig goes stale | After cluster re-init, TLS certs change. Fetch a fresh kubeconfig from the target cluster. |
| Ceph admin keyring | Keep Ceph admin credentials outside this repository and use the operator runbook for break-glass work. |
| Environment values | Registry, endpoints, and credentials belong in `k8s/env/<env>/` templates or ignored local overrides. |
| S3 test needs Django setup | Must `export DJANGO_SETTINGS_MODULE=settings` and call `django.setup()`. |

#!/bin/bash
# ==============================================================================
# dev.sh — Unified Deployment + Development Tool for S3 Bucket Manager
# ==============================================================================
#
# PURPOSE:
#   Single entrypoint for both full deployment and fast inner-loop redeploys.
#
# USAGE:
#   ./dev.sh deploy --env dev --rebuild   # Full deploy with dev overlays
#   ./dev.sh deploy --env prod             # Full deploy with production safety gates
#   ./dev.sh backend          # Rebuild + redeploy backend only
#   ./dev.sh frontend         # Rebuild + redeploy frontend only
#   ./dev.sh all              # Rebuild + redeploy both
#   ./dev.sh status           # Pods, port-forwards, Ceph health at a glance
#   ./dev.sh logs [component] # Tail logs (backend|frontend|authentik)
#   ./dev.sh access           # Start port-forwards if not running
#   ./dev.sh restart backend  # Just restart pod (no rebuild, e.g. config change)
#   ./dev.sh check            # Full infrastructure health check (VMs, K3s, Ceph)
#   ./dev.sh cleanup          # Delete K8s namespace/resources
#
# WHAT EACH SUBCOMMAND DOES (for manual reference):
#
#   deploy:
#     1. Bootstrap missing frontend generated dirs (npm ci, npm run build)
#     2. Apply the selected env overlay and base manifests in dependency order
#     3. Configure Authentik OAuth2 and wait for workloads to become healthy
#
#   backend/frontend/all:
#     1. podman build -t ${REGISTRY}/s3mgr-<component>:latest
#     2. podman push (K3s nodes pull automatically from configured registry)
#     3. kubectl rollout restart deployment/<component>
#     4. kubectl rollout status (wait for ready)
#
#   status:
#     1. kubectl get pods -n storage-system
#     2. Check port-forwards (ss -tlnp)
#     3. Quick Ceph health via ssh to admin node
#
#   access:
#     1. Check/establish SSH tunnel to K3s API (port 16443)
#     2. Start kubectl port-forwards for frontend (3000) and authentik (9000)
#
# PREREQUISITES:
#   - SSH tunnel to K3s API running (or let 'access' set it up)
#   - KUBECONFIG set to /tmp/k3s-tunnel-kubeconfig.yaml
#   - Environment overlays available under k8s/env/<env>
#   - Stencil-specific node, registry, and RGW defaults reviewed below
#
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

NAMESPACE="storage-system"
K3S_NODES=("192.168.132.10" "192.168.132.11" "192.168.132.12")
TUNNEL_SOCK="/tmp/k3s-api-tunnel.sock"
TUNNEL_KUBECONFIG="/tmp/k3s-tunnel-kubeconfig.yaml"
MANIFESTS_DIR="$SCRIPT_DIR/manifests"
ENV_BASE_DIR="$SCRIPT_DIR/env"

REGISTRY="registry.stencil.com:5000"
BACKEND_IMAGE="${REGISTRY}/s3mgr-backend:latest"
FRONTEND_IMAGE="${REGISTRY}/s3mgr-frontend:latest"

DEPLOY_ENV="${DEPLOY_ENV:-dev}"
ENV_DIR=""
BACKEND_CONFIG_FILE=""
SECRETS_FILE=""
AUTHENTIK_SERVICE_FILE=""

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'
BOLD='\033[1m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[  OK]${NC} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $*"; }
error()   { echo -e "${RED}[FAIL]${NC} $*"; }
fail()    { error "$*"; exit 1; }
step()    { echo -e "\n${CYAN}${BOLD}━━━ $* ━━━${NC}"; }

prepare_frontend_generated_dirs() {
    local frontend_dir="$PROJECT_DIR/frontend"
    local needs_install=false
    local needs_build=false

    [ -d "$frontend_dir/node_modules" ] || needs_install=true
    [ -d "$frontend_dir/dist" ] || needs_build=true

    if [ "$needs_install" = false ] && [ "$needs_build" = false ]; then
        return 0
    fi

    if ! command -v npm &>/dev/null; then
        warn "npm not found; skipping local frontend bootstrap (container build will still generate artifacts)."
        return 0
    fi

    step "Bootstrapping missing frontend generated directories"
    (
        cd "$frontend_dir"
        if [ "$needs_install" = true ]; then
            info "Creating frontend/node_modules via npm ci"
            npm ci
        fi
        if [ "$needs_build" = true ]; then
            info "Creating frontend/dist via npm run build"
            npm run build
        fi
    )
    success "Frontend generated directories are present"
}

rollout_with_retry() {
    local resource="$1"
    local timeout="${2:-120s}"
    local attempts="${3:-2}"
    local attempt

    for ((attempt=1; attempt<=attempts; attempt++)); do
        if kubectl rollout status "$resource" -n "$NAMESPACE" --timeout="$timeout"; then
            return 0
        fi
        warn "$resource rollout attempt ${attempt}/${attempts} failed"
        if [ "$attempt" -lt "$attempts" ]; then
            info "Retrying rollout wait for $resource..."
        fi
    done

    fail "$resource rollout did not complete after ${attempts} attempts"
}

# ==============================================================================
# Environment file selection + production safety gates
# ==============================================================================

resolve_environment_files() {
    case "$DEPLOY_ENV" in
        dev|prod) ;;
        *) fail "Unsupported --env '$DEPLOY_ENV' (expected: dev|prod)" ;;
    esac

    ENV_DIR="$ENV_BASE_DIR/$DEPLOY_ENV"
    BACKEND_CONFIG_FILE="$ENV_DIR/backend-config.yaml"
    SECRETS_FILE="$ENV_DIR/secrets.yaml"

    local backend_local="$ENV_DIR/backend-config.local.yaml"
    local secrets_local="$ENV_DIR/secrets.local.yaml"

    [ -f "$backend_local" ] && BACKEND_CONFIG_FILE="$backend_local"
    [ -f "$secrets_local" ] && SECRETS_FILE="$secrets_local"

    [ -f "$BACKEND_CONFIG_FILE" ] || fail "Missing env config file: $BACKEND_CONFIG_FILE"
    [ -f "$SECRETS_FILE" ] || fail "Missing env secrets file: $SECRETS_FILE"

    AUTHENTIK_SERVICE_FILE=""
    if [ "$DEPLOY_ENV" = "dev" ]; then
        local nodeport_local="$ENV_DIR/authentik-service-nodeport.local.yaml"
        local nodeport_default="$ENV_DIR/authentik-service-nodeport.yaml"
        if [ -f "$nodeport_local" ]; then
            AUTHENTIK_SERVICE_FILE="$nodeport_local"
        elif [ -f "$nodeport_default" ]; then
            AUTHENTIK_SERVICE_FILE="$nodeport_default"
        fi
    fi

    if [[ "$BACKEND_CONFIG_FILE" == *.local.yaml ]] || [[ "$SECRETS_FILE" == *.local.yaml ]] || [[ "$AUTHENTIK_SERVICE_FILE" == *.local.yaml ]]; then
        info "Using local overrides from $ENV_DIR (*.local.*)"
    fi
}

run_production_safety_checks() {
    if [ "$DEPLOY_ENV" != "prod" ]; then
        return 0
    fi

    grep -q "REPLACE_WITH_PROD_" "$SECRETS_FILE" && fail "Prod secrets file still contains placeholders: $SECRETS_FILE"
    grep -q "REPLACE_WITH_PROD_" "$BACKEND_CONFIG_FILE" && fail "Prod config file still contains placeholders: $BACKEND_CONFIG_FILE"

    grep -q 'DJANGO_DEBUG: "True"' "$BACKEND_CONFIG_FILE" && fail "Prod config has DJANGO_DEBUG=True"
    grep -q 'DJANGO_ALLOWED_HOSTS: "\*"' "$BACKEND_CONFIG_FILE" && fail "Prod config has wildcard DJANGO_ALLOWED_HOSTS"
    grep -q 'S3_VERIFY_SSL: "False"' "$BACKEND_CONFIG_FILE" && fail "Prod config has S3_VERIFY_SSL=False"
    grep -q 'AUTHENTIK_EXTERNAL_URL: "http://localhost:9000"' "$BACKEND_CONFIG_FILE" && fail "Prod config uses localhost Authentik URL"
    if [ -n "${PUBLIC_APP_URL:-}" ] && [[ "$PUBLIC_APP_URL" == *"localhost"* ]]; then
        fail "PUBLIC_APP_URL points to localhost in prod: $PUBLIC_APP_URL"
    fi
}

# ==============================================================================
# Ensure kubeconfig is set
# ==============================================================================

ensure_kubeconfig() {
    export KUBECONFIG="${KUBECONFIG:-$TUNNEL_KUBECONFIG}"
    if [ ! -f "$KUBECONFIG" ]; then
        error "Kubeconfig not found: $KUBECONFIG"
        echo "  Run: ./dev.sh access"
        exit 1
    fi
    if ! kubectl cluster-info &>/dev/null 2>&1; then
        error "Cannot connect to K3s. SSH tunnel may be down."
        echo "  Run: ./dev.sh access"
        exit 1
    fi
}

# ==============================================================================
# Build + Load + Restart a single component
# ==============================================================================

redeploy_component() {
    local component="$1"  # "backend" or "frontend"
    local image_name build_dir

    if [ "$component" = "backend" ]; then
        image_name="$BACKEND_IMAGE"
        build_dir="$PROJECT_DIR/backend"
    elif [ "$component" = "frontend" ]; then
        image_name="$FRONTEND_IMAGE"
        build_dir="$PROJECT_DIR/frontend"
    else
        error "Unknown component: $component"
        exit 1
    fi

    local start_time=$(date +%s)

    if [ "$component" = "frontend" ]; then
        prepare_frontend_generated_dirs
    fi

    # Step 1: Build
    step "Building $component"
    podman build -t "$image_name" -f "$build_dir/Containerfile" "$build_dir"
    success "Image built"

    # Step 2: Push to Stencil registry (all K3s nodes pull automatically)
    info "Pushing to registry..."
    podman push "$image_name"
    success "Image pushed to registry"

    # Step 3: Restart deployment (K3s pulls new image from registry)
    info "Restarting deployment..."
    kubectl rollout restart "deployment/$component" -n "$NAMESPACE"
    rollout_with_retry "deployment/$component" "180s" "2"

    local elapsed=$(( $(date +%s) - start_time ))
    success "$component redeployed in ${elapsed}s"
}

build_component_image() {
    local component="$1"
    local image_name build_dir

    if [ "$component" = "backend" ]; then
        image_name="$BACKEND_IMAGE"
        build_dir="$PROJECT_DIR/backend"
    elif [ "$component" = "frontend" ]; then
        image_name="$FRONTEND_IMAGE"
        build_dir="$PROJECT_DIR/frontend"
    else
        fail "Unknown component for build: $component"
    fi

    if [ "$component" = "frontend" ]; then
        prepare_frontend_generated_dirs
    fi

    step "Building $component image"
    podman build -t "$image_name" -f "$build_dir/Containerfile" "$build_dir"
    success "$component image built"

    info "Pushing $component image to registry..."
    podman push "$image_name"
    success "$component image pushed"
}

wait_for_rollout() {
    local resource="$1"
    local timeout="${2:-300s}"
    local attempts="${3:-2}"
    info "Waiting for $resource..."
    rollout_with_retry "$resource" "$timeout" "$attempts"
    success "$resource is ready"
}

apply_manifests_for_env() {
    step "Applying manifests for env=$DEPLOY_ENV"

    kubectl apply -f "$MANIFESTS_DIR/00-namespace.yaml"
    kubectl apply -f "$SECRETS_FILE"
    kubectl apply -f "$BACKEND_CONFIG_FILE"

    kubectl apply -f "$MANIFESTS_DIR/01-authentik-postgres.yaml"
    kubectl apply -f "$MANIFESTS_DIR/02-authentik-redis.yaml"
    kubectl apply -f "$MANIFESTS_DIR/04-django-postgres.yaml"

    wait_for_rollout "deployment/authentik-postgres"
    wait_for_rollout "deployment/authentik-redis"
    wait_for_rollout "deployment/django-postgres"

    kubectl apply -f "$MANIFESTS_DIR/03-authentik-server.yaml"
    wait_for_rollout "deployment/authentik-server"
    wait_for_rollout "deployment/authentik-worker"

    if [ -n "$AUTHENTIK_SERVICE_FILE" ]; then
        kubectl apply -f "$AUTHENTIK_SERVICE_FILE"
        success "Applied dev Authentik NodePort service override"
    fi

    kubectl apply -f "$MANIFESTS_DIR/05-backend.yaml"
    wait_for_rollout "deployment/backend"

    kubectl apply -f "$MANIFESTS_DIR/06-frontend.yaml"
    wait_for_rollout "deployment/frontend"

    kubectl apply -f "$MANIFESTS_DIR/07-cronjob.yaml"
    success "CronJob applied"
}

get_secret_value() {
    local secret_name="$1"
    local secret_key="$2"
    kubectl get secret "$secret_name" -n "$NAMESPACE" -o "jsonpath={.data.${secret_key}}" | base64 -d
}

get_config_value() {
    local config_name="$1"
    local config_key="$2"
    kubectl get configmap "$config_name" -n "$NAMESPACE" -o "jsonpath={.data.${config_key}}"
}

configure_authentik_from_cluster() {
    step "Configuring Authentik OAuth2 provider"

    local authentik_pod
    authentik_pod=$(kubectl get pods -n "$NAMESPACE" -l app=authentik-server \
        --no-headers -o custom-columns=":metadata.name" | head -1)
    [ -z "$authentik_pod" ] && fail "Authentik server pod not found"

    local oidc_secret bootstrap_password oidc_client_id public_app_url
    oidc_secret=$(get_secret_value backend-secret oidc-client-secret)
    bootstrap_password=$(get_secret_value authentik-secret bootstrap-password)
    oidc_client_id=$(get_config_value backend-config OIDC_CLIENT_ID)
    if [ "$DEPLOY_ENV" = "prod" ]; then
        public_app_url="${PUBLIC_APP_URL:-}"
        [ -z "$public_app_url" ] && fail "PUBLIC_APP_URL is required for prod Authentik config"
    else
        public_app_url="${PUBLIC_APP_URL:-http://localhost:3000}"
    fi

    kubectl cp "$SCRIPT_DIR/configure_authentik.py" \
        "$NAMESPACE/$authentik_pod:/tmp/configure_authentik.py"

    kubectl exec -n "$NAMESPACE" "$authentik_pod" -- env \
        OIDC_CLIENT_ID="$oidc_client_id" \
        OIDC_CLIENT_SECRET="$oidc_secret" \
        AUTHENTIK_BOOTSTRAP_PASSWORD="$bootstrap_password" \
        PUBLIC_APP_URL="$public_app_url" \
        python /tmp/configure_authentik.py

    kubectl rollout restart deployment/backend -n "$NAMESPACE"
    kubectl rollout status deployment/backend -n "$NAMESPACE" --timeout=180s
    success "Authentik configured and backend restarted"
}

deploy_all() {
    local rebuild=false
    local skip_authentik=false

    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)
                echo "Usage: ./dev.sh deploy [--env dev|prod] [--rebuild] [--skip-authentik-config]"
                return 0
                ;;
            --rebuild)
                rebuild=true
                ;;
            --skip-authentik-config)
                skip_authentik=true
                ;;
            --env)
                [ -z "${2:-}" ] && fail "Usage: ./dev.sh deploy --env <dev|prod> [--rebuild]"
                DEPLOY_ENV="$2"
                shift
                ;;
            *)
                fail "Unknown deploy argument: $1"
                ;;
        esac
        shift
    done

    resolve_environment_files
    run_production_safety_checks

    if [ ! -f "$TUNNEL_KUBECONFIG" ]; then
        setup_access
    fi
    ensure_kubeconfig

    step "Deploy start (env=$DEPLOY_ENV)"

    if [ "$rebuild" = true ]; then
        build_component_image backend
        build_component_image frontend
    else
        info "Skipping image rebuild (use --rebuild to build and push both images)"
    fi

    apply_manifests_for_env

    if [ "$skip_authentik" = false ]; then
        configure_authentik_from_cluster
    else
        warn "Skipping Authentik configuration by request"
    fi

    success "Deploy completed for env=$DEPLOY_ENV"
}

# ==============================================================================
# Status: pods, port-forwards, Ceph quick check
# ==============================================================================

show_status() {
    ensure_kubeconfig

    step "Pod Status"
    kubectl get pods -n "$NAMESPACE" -o wide 2>/dev/null || warn "Cannot reach K3s"

    step "Port-Forwards"
    local pf_frontend pf_authentik
    pf_frontend=$(ss -tlnp 2>/dev/null | grep ":3000 " || true)
    pf_authentik=$(ss -tlnp 2>/dev/null | grep ":9000 " || true)

    if [ -n "$pf_frontend" ]; then
        success "Frontend  :3000 → active"
    else
        warn "Frontend  :3000 → NOT running"
        info "Fix: nohup kubectl port-forward svc/frontend-service 3000:80 -n storage-system > /tmp/pf-frontend.log 2>&1 &"
    fi

    if [ -n "$pf_authentik" ]; then
        success "Authentik :9000 → active"
    else
        warn "Authentik :9000 → NOT running"
        info "Fix: nohup kubectl port-forward svc/authentik-service 9000:9000 -n storage-system > /tmp/pf-authentik.log 2>&1 &"
    fi

    step "SSH Tunnel"
    if [ -S "$TUNNEL_SOCK" ]; then
        success "K3s API tunnel active ($TUNNEL_SOCK)"
    else
        warn "K3s API tunnel NOT running"
        info "Fix: ./dev.sh access"
    fi

    step "Ceph Quick Check"
    local ceph_admin="192.168.132.90"
    if ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o BatchMode=yes \
        "root@${ceph_admin}" "echo ok" &>/dev/null 2>&1; then
        local health
        health=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no \
            "root@${ceph_admin}" "ceph health 2>/dev/null" 2>/dev/null || echo "UNKNOWN")
        if [ "$health" = "HEALTH_OK" ]; then
            success "Ceph: $health"
        else
            warn "Ceph: $health"
        fi
        # RGW check
        local rgw_code
        rgw_code=$(curl -sk -o /dev/null -w '%{http_code}' --connect-timeout 3 "https://192.168.132.110/" 2>/dev/null || echo "000")
        if [ "$rgw_code" = "200" ] || [ "$rgw_code" = "403" ]; then
            success "RGW: HTTP $rgw_code (OK)"
        else
            warn "RGW: HTTP $rgw_code"
        fi
    else
        warn "Ceph admin node unreachable"
    fi

    echo ""
}

# ==============================================================================
# Logs: tail component logs
# ==============================================================================

show_logs() {
    ensure_kubeconfig
    local component="${1:-backend}"

    case "$component" in
        backend|frontend|authentik-server|authentik-worker)
            kubectl logs -l "app=$component" -n "$NAMESPACE" --tail=50 -f
            ;;
        *)
            error "Unknown component: $component (use: backend, frontend, authentik-server)"
            exit 1
            ;;
    esac
}

# ==============================================================================
# Access: establish tunnel + port-forwards
# ==============================================================================

setup_access() {
    step "Setting up access"

    # 1. SSH tunnel to K3s API
    if [ -S "$TUNNEL_SOCK" ]; then
        if ssh -S "$TUNNEL_SOCK" -O check root@dummy &>/dev/null 2>&1; then
            success "SSH tunnel already running"
        else
            warn "Stale tunnel socket — cleaning up"
            rm -f "$TUNNEL_SOCK"
        fi
    fi

    if [ ! -S "$TUNNEL_SOCK" ]; then
        info "Starting SSH tunnel: localhost:16443 → K3s API..."
        ssh -fNM -S "$TUNNEL_SOCK" \
            -L 16443:127.0.0.1:6443 \
            -o StrictHostKeyChecking=no \
            -o ExitOnForwardFailure=yes \
            root@192.168.132.10
        success "SSH tunnel established"
    fi

    # 2. Fetch kubeconfig if missing
    if [ ! -f "$TUNNEL_KUBECONFIG" ]; then
        info "Fetching kubeconfig..."
        scp -o StrictHostKeyChecking=no \
            root@192.168.132.10:/etc/rancher/k3s/k3s.yaml "$TUNNEL_KUBECONFIG" &>/dev/null
        sed -i 's|server: https://127.0.0.1:6443|server: https://127.0.0.1:16443|' \
            "$TUNNEL_KUBECONFIG"
        success "Kubeconfig ready"
    fi

    export KUBECONFIG="$TUNNEL_KUBECONFIG"

    # 3. Port-forwards
    if ! ss -tlnp 2>/dev/null | grep -q ":3000 "; then
        info "Starting frontend port-forward (3000)..."
        nohup kubectl port-forward svc/frontend-service 3000:80 -n "$NAMESPACE" \
            > /tmp/pf-frontend.log 2>&1 &
        sleep 1
        if ss -tlnp 2>/dev/null | grep -q ":3000 "; then
            success "Frontend :3000 → active"
        else
            warn "Frontend port-forward may have failed — check /tmp/pf-frontend.log"
        fi
    else
        success "Frontend :3000 already running"
    fi

    if ! ss -tlnp 2>/dev/null | grep -q ":9000 "; then
        info "Starting Authentik port-forward (9000)..."
        nohup kubectl port-forward svc/authentik-service 9000:9000 -n "$NAMESPACE" \
            > /tmp/pf-authentik.log 2>&1 &
        sleep 1
        if ss -tlnp 2>/dev/null | grep -q ":9000 "; then
            success "Authentik :9000 → active"
        else
            warn "Authentik port-forward may have failed — check /tmp/pf-authentik.log"
        fi
    else
        success "Authentik :9000 already running"
    fi

    echo ""
    success "Access ready!"
    echo ""
    echo -e "  ${BOLD}On your LOCAL machine:${NC}"
    echo "    ssh -L 3000:localhost:3000 -L 9000:localhost:9000 orfeo-vm"
    echo ""
    echo -e "  ${BOLD}Then open:${NC} ${GREEN}http://localhost:3000${NC}"
    echo ""
}

# ==============================================================================
# Restart: just restart a deployment (no rebuild)
# ==============================================================================

restart_component() {
    local component="$1"
    ensure_kubeconfig

    info "Restarting $component..."
    kubectl rollout restart "deployment/$component" -n "$NAMESPACE"
    rollout_with_retry "deployment/$component" "180s" "2"
    success "$component restarted"
}

# ==============================================================================
# dev-db: Local PostgreSQL via podman (for running Django without K3s)
#
# Starts a postgres:15-alpine container with credentials that exactly match
# 04-django-postgres.yaml so that settings.py picks up PostgreSQL instead of
# falling back to SQLite.
#
# After running this, export the env vars and use Django normally:
#
#   ./dev.sh dev-db start
#   export DATABASE_HOST=localhost DATABASE_NAME=djangodb \
#          DATABASE_USER=djangouser DATABASE_PASSWORD=<dev-db-password>
#   cd ../backend && python manage.py migrate && python manage.py runserver
#
# ==============================================================================

DEV_PG_CONTAINER="dev-postgres"
DEV_PG_IMAGE="postgres:15-alpine"
DEV_PG_DB="djangodb"
DEV_PG_USER="djangouser"
DEV_PG_PASS="dev-db-password"

dev_db() {
    local action="${1:-help}"
    case "$action" in
        start)
            if podman inspect "$DEV_PG_CONTAINER" &>/dev/null; then
                local state
                state=$(podman inspect --format '{{.State.Status}}' "$DEV_PG_CONTAINER")
                if [ "$state" = "running" ]; then
                    info "dev-postgres is already running on localhost:5432"
                else
                    info "Starting existing dev-postgres container..."
                    podman start "$DEV_PG_CONTAINER"
                fi
            else
                info "Creating and starting dev-postgres container..."
                podman run -d \
                    --name "$DEV_PG_CONTAINER" \
                    -e POSTGRES_DB="$DEV_PG_DB" \
                    -e POSTGRES_USER="$DEV_PG_USER" \
                    -e POSTGRES_PASSWORD="$DEV_PG_PASS" \
                    -p 5432:5432 \
                    "$DEV_PG_IMAGE"
            fi
            echo ""
            success "PostgreSQL is running. Export these vars then use Django normally:"
            echo ""
            echo "  export DATABASE_HOST=localhost"
            echo "  export DATABASE_NAME=$DEV_PG_DB"
            echo "  export DATABASE_USER=$DEV_PG_USER"
            echo "  export DATABASE_PASSWORD=$DEV_PG_PASS"
            echo ""
            echo "  cd ../backend"
            echo "  python manage.py migrate"
            echo "  python manage.py runserver"
            echo ""
            ;;
        stop)
            info "Stopping dev-postgres..."
            podman stop "$DEV_PG_CONTAINER" && success "Stopped"
            ;;
        destroy)
            info "Removing dev-postgres container and all data..."
            podman rm -f "$DEV_PG_CONTAINER" && success "Removed"
            ;;
        status)
            if podman inspect "$DEV_PG_CONTAINER" &>/dev/null; then
                local state
                state=$(podman inspect --format '{{.State.Status}}' "$DEV_PG_CONTAINER")
                info "dev-postgres: $state"
            else
                info "dev-postgres: not created"
            fi
            ;;
        *)
            echo "  Usage: ./dev.sh dev-db <start|stop|destroy|status>"
            echo ""
            echo "  start    Create and start local PostgreSQL (localhost:5432)"
            echo "  stop     Stop the container (data preserved)"
            echo "  destroy  Remove container and all data"
            echo "  status   Show container state"
            ;;
    esac
}

# ==============================================================================
# Check: full infrastructure health check
# ==============================================================================

CEPH_SVC_NODES=("192.168.132.80" "192.168.132.81" "192.168.132.82")
CEPH_OSD_NODES=("192.168.132.90" "192.168.132.91" "192.168.132.92")
CEPH_ADMIN="192.168.132.90"
DISK_WARN_THRESHOLD=80
DISK_CRIT_THRESHOLD=95

run_check() {
    local pass_fn fail_fn warn_fn info_fn ERRORS=0
    pass_fn()  { echo -e "  ${GREEN}✓${NC} $*"; }
    fail_fn()  { echo -e "  ${RED}✗${NC} $*"; ERRORS=$((ERRORS + 1)); }
    warn_fn()  { echo -e "  ${YELLOW}!${NC} $*"; }
    info_fn()  { echo -e "  ${BLUE}→${NC} $*"; }

    echo ""
    echo -e "${CYAN}${BOLD}━━━ Infrastructure Health Check ━━━${NC}"

    # 1. Libvirt VMs
    echo ""
    echo -e "${BOLD}1. Libvirt VMs${NC}"
    if ! command -v virsh &>/dev/null; then
        warn_fn "virsh not found — skipping VM checks"
    else
        local all_vms
        all_vms=$(virsh list --all --name 2>/dev/null | grep -v '^$' || true)
        if [ -z "$all_vms" ]; then
            fail_fn "No libvirt VMs found"
        else
            for vm_pattern in kube ceph; do
                local matching
                matching=$(echo "$all_vms" | grep -i "$vm_pattern" || true)
                while IFS= read -r vm; do
                    [ -z "$vm" ] && continue
                    local state
                    state=$(virsh domstate "$vm" 2>/dev/null || echo "unknown")
                    if [ "$state" = "running" ]; then
                        pass_fn "$vm"
                    else
                        warn_fn "$vm is $state — starting..."
                        if virsh start "$vm" &>/dev/null; then
                            pass_fn "$vm started"
                        else
                            fail_fn "Failed to start $vm"
                        fi
                    fi
                done <<< "$matching"
            done
        fi
    fi

    # 2. K3s Cluster
    echo ""
    echo -e "${BOLD}2. K3s Cluster${NC}"
    local k3s_found=false
    for node in "${K3S_NODES[@]}"; do
        if ! ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o BatchMode=yes \
            "root@${node}" "echo ok" &>/dev/null; then
            fail_fn "$node — SSH unreachable"
            continue
        fi
        if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no -o BatchMode=yes \
            "root@${node}" "systemctl is-active k3s-server" &>/dev/null; then
            pass_fn "$node — SSH ✓  k3s-server ✓"
            k3s_found=true
        else
            warn_fn "$node — SSH ✓  k3s-server ✗"
        fi
    done
    if ! $k3s_found; then
        fail_fn "k3s-server not running on any node"
    fi

    # 3. Ceph RGW
    echo ""
    echo -e "${BOLD}3. Ceph RGW (S3 Storage)${NC}"
    if ping -c 1 -W 3 "192.168.132.110" &>/dev/null; then
        pass_fn "RGW VIP (192.168.132.110) reachable"
        local http_code
        http_code=$(curl -sk -o /dev/null -w '%{http_code}' --connect-timeout 5 \
            "https://192.168.132.110/" 2>/dev/null || echo "000")
        if [ "$http_code" = "403" ] || [ "$http_code" = "200" ]; then
            pass_fn "RGW HTTP $http_code (healthy)"
        elif [ "$http_code" = "503" ]; then
            fail_fn "RGW HTTP 503 — daemon likely down"
            info_fn "Fix: ssh root@$CEPH_ADMIN 'ceph orch ps --daemon-type rgw'"
        elif [ "$http_code" = "000" ]; then
            warn_fn "VIP reachable but HTTPS failed (HAProxy may be down)"
        else
            warn_fn "RGW returned unexpected HTTP $http_code"
        fi
    else
        fail_fn "RGW VIP (192.168.132.110) NOT reachable"
    fi

    # 4. Ceph Node Disk Space
    echo ""
    echo -e "${BOLD}4. Ceph Node Disk Space${NC}"
    for node in "${CEPH_SVC_NODES[@]}" "${CEPH_OSD_NODES[@]}"; do
        if ! ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o BatchMode=yes \
            "root@${node}" "echo ok" &>/dev/null; then
            warn_fn "$node — unreachable"
            continue
        fi
        local disk_info usage total avail
        disk_info=$(ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "root@${node}" \
            "df -h --output=pcent,size,avail / | tail -1" 2>/dev/null || echo "0 ? ?")
        usage=$(echo "$disk_info" | awk '{gsub(/%/,""); print $1}')
        total=$(echo "$disk_info" | awk '{print $2}')
        avail=$(echo "$disk_info" | awk '{print $3}')
        if [ "$usage" -ge "$DISK_CRIT_THRESHOLD" ] 2>/dev/null; then
            fail_fn "$node — ${usage}% used (${avail} free / ${total}) — CRITICAL"
            info_fn "Fix: ssh root@$node 'journalctl --vacuum-size=5M && dnf clean all'"
        elif [ "$usage" -ge "$DISK_WARN_THRESHOLD" ] 2>/dev/null; then
            warn_fn "$node — ${usage}% used (${avail} free / ${total})"
        else
            pass_fn "$node — ${usage}% used (${avail} free / ${total})"
        fi
    done

    # 5. Ceph Cluster Health
    echo ""
    echo -e "${BOLD}5. Ceph Cluster Health${NC}"
    if ! ssh -o ConnectTimeout=3 -o StrictHostKeyChecking=no -o BatchMode=yes \
        "root@${CEPH_ADMIN}" "echo ok" &>/dev/null; then
        warn_fn "Ceph admin node ($CEPH_ADMIN) unreachable"
    else
        local health
        health=$(ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
            "root@${CEPH_ADMIN}" "ceph health 2>/dev/null" 2>/dev/null || echo "UNKNOWN")
        if [[ "$health" == "HEALTH_OK"* ]]; then
            pass_fn "Cluster: HEALTH_OK"
        elif [[ "$health" == "HEALTH_WARN"* ]]; then
            warn_fn "Cluster: $health"
            ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
                "root@${CEPH_ADMIN}" "ceph health detail 2>/dev/null" 2>/dev/null \
                | head -5 | while read -r line; do info_fn "  $line"; done
        elif [[ "$health" == "HEALTH_ERR"* ]]; then
            fail_fn "Cluster: $health"
        else
            warn_fn "Could not determine health: $health"
        fi
        local degraded
        degraded=$(ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no \
            "root@${CEPH_ADMIN}" "ceph orch ls --format json 2>/dev/null" 2>/dev/null \
            | python3 -c "
import sys, json
for s in json.load(sys.stdin):
    r = s.get('status',{}).get('running',0)
    sz = s.get('status',{}).get('size',0)
    if r < sz: print(f'{s.get(\"service_name\",\"?\")}: {r}/{sz}')
" 2>/dev/null || true)
        if [ -z "$degraded" ]; then
            pass_fn "All services at full capacity"
        else
            while read -r line; do warn_fn "Degraded: $line"; done <<< "$degraded"
        fi
    fi

    # 6. K8s Workloads
    echo ""
    echo -e "${BOLD}6. K8s Workloads${NC}"
    local kc="${KUBECONFIG:-$TUNNEL_KUBECONFIG}"
    if [ -f "$kc" ] && KUBECONFIG="$kc" kubectl cluster-info &>/dev/null 2>&1; then
        export KUBECONFIG="$kc"
        local total running
        total=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | wc -l)
        running=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep -c "Running" || true)
        if [ "$total" -eq 0 ]; then
            warn_fn "No pods in $NAMESPACE namespace (not deployed yet?)"
        elif [ "$running" -eq "$total" ]; then
            pass_fn "Pods: $running/$total Running"
        else
            warn_fn "Pods: $running/$total Running"
            kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null \
                | grep -v "Running" | while read -r line; do info_fn "  $line"; done
        fi
    else
        warn_fn "kubectl not connected (run: ./dev.sh access)"
    fi

    # Summary
    echo ""
    echo -e "${CYAN}${BOLD}━━━ Summary ━━━${NC}"
    echo ""
    if [ $ERRORS -eq 0 ]; then
        echo -e "  ${GREEN}${BOLD}ALL CHECKS PASSED${NC}"
    else
        echo -e "  ${RED}${BOLD}$ERRORS CHECK(S) FAILED${NC}"
    fi
    echo ""
    return $ERRORS
}

# ==============================================================================
# Cleanup: delete namespace + optionally clean images
# ==============================================================================

run_cleanup() {
    ensure_kubeconfig

    echo ""
    echo -e "${BOLD}S3 Bucket Manager — Cleanup${NC}"
    echo ""

    info "Deleting all resources in namespace '$NAMESPACE'..."
    kubectl delete namespace "$NAMESPACE" --ignore-not-found --timeout=60s 2>/dev/null || true
    success "Namespace deleted"

    read -p "Remove container images from K3s nodes? [y/N] " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        for node in "${K3S_NODES[@]}"; do
            info "Removing images from $node..."
            ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 "root@${node}" \
                "k3s ctr images rm ${REGISTRY}/s3mgr-backend:latest ${REGISTRY}/s3mgr-frontend:latest" \
                2>/dev/null || warn "Could not clean images on $node"
        done
        success "Images cleaned from all nodes"
    fi

    echo ""
    success "Cleanup complete!"
    echo ""
    echo "  To verify:  kubectl get all -n $NAMESPACE"
    echo "  To redeploy: ./dev.sh deploy --env dev --rebuild"
    echo ""
}

# ==============================================================================
# Main
# ==============================================================================

usage() {
    echo -e "${BOLD}dev.sh — Unified Deploy + Dev Tool${NC}"
    echo ""
    echo "  Usage: ./dev.sh <command> [args]"
    echo ""
    echo "  Commands:"
    echo "    deploy [--env dev|prod] [--rebuild] [--skip-authentik-config]"
    echo "                         Full deploy using k8s/env/<env> overlays"
    echo "    backend              Rebuild + redeploy backend (~60s)"
    echo "    frontend             Rebuild + redeploy frontend (~60s)"
    echo "    all                  Rebuild + redeploy both"
    echo "    status               Show pods, port-forwards, Ceph health"
    echo "    logs [component]     Tail logs (backend, frontend, authentik-server)"
    echo "    access               Set up SSH tunnel + port-forwards"
    echo "    restart <comp>       Restart deployment without rebuild"
    echo "    check                Full infrastructure health check (VMs, K3s, Ceph, disks)"
    echo "    cleanup              Delete namespace + optionally clean images"
    echo "    dev-db <action>      Local PostgreSQL via podman (start|stop|destroy|status)"
    echo ""
    echo "  Environment overlays:"
    echo "    k8s/env/dev/{backend-config.yaml,secrets.yaml}"
    echo "    k8s/env/prod/{backend-config.yaml,secrets.yaml}"
    echo "    Optional local overrides (gitignored): *.local.yaml"
    echo ""
}

COMMAND="${1:-}"
shift || true

case "$COMMAND" in
    deploy)
        deploy_all "$@"
        ;;
    backend)
        ensure_kubeconfig
        redeploy_component backend
        ;;
    frontend)
        ensure_kubeconfig
        redeploy_component frontend
        ;;
    all)
        ensure_kubeconfig
        redeploy_component backend
        redeploy_component frontend
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "${1:-backend}"
        ;;
    access)
        setup_access
        ;;
    restart)
        if [ -z "${1:-}" ]; then
            fail "Usage: ./dev.sh restart <component>"
        fi
        restart_component "$1"
        ;;
    check|preflight)
        run_check
        ;;
    cleanup)
        run_cleanup
        ;;
    dev-db)
        dev_db "${1:-help}"
        ;;
    *)
        usage
        exit 1
        ;;
esac

#!/bin/bash
# ==============================================================================
# infra.sh — Infrastructure Layer Management (Authentik Identity Services)
# ==============================================================================
#
# PURPOSE:
#   Manages the Authentik identity provider in the authentik-bucket-explorer
#   namespace. This is the INFRASTRUCTURE layer — Authentik must be deployed
#   and configured before the webapp can start (app.sh handles the webapp layer).
#
#   The two scripts are self-contained and do not call each other. Deploy infra
#   first, then deploy the webapp: ./infra.sh deploy && ./app.sh deploy.
#
# USAGE:
#   ./infra.sh deploy [--skip-config]
#                         Deploy Authentik: namespace, secrets, databases, server
#                         Then configure OAuth2 provider for the webapp
#
#   ./infra.sh configure
#                         Re-run OAuth2 configuration only (no manifest re-apply)
#                         Useful after manual Authentik changes or credential rotation
#
#   ./infra.sh status     Show pods in authentik-bucket-explorer namespace
#   ./infra.sh logs [authentik-server|authentik-worker]
#                         Tail logs from an Authentik component (default: authentik-server)
#   ./infra.sh restart <component>
#                         Restart an Authentik deployment without a full redeploy
#   ./infra.sh cleanup    Delete authentik-bucket-explorer namespace and all its resources
#   ./infra.sh check      Full infrastructure health check: VMs, K3s, Ceph, disk space,
#                         and workload status in both namespaces
#
# WHAT EACH SUBCOMMAND DOES (for manual reference):
#
#   deploy:
#     1. Apply authentik-bucket-explorer namespace
#     2. Apply infra secrets (authentik-secret)
#     3. Apply Authentik PostgreSQL + Redis; wait for both to be ready
#     4. Apply Authentik server + worker; wait for both to be ready
#     5. (dev only) Apply NodePort service for local Authentik UI access
#     6. Run configure_authentik.py inside the pod to register the webapp OAuth2 provider
#
#   configure:
#     1. Find running Authentik pod in authentik-bucket-explorer namespace
#     2. Read OIDC credentials from bucket-explorer/backend-secret
#     3. Run configure_authentik.py via kubectl exec to set up OAuth2 provider + application
#     4. Restart the webapp backend (if already deployed) so it picks up the new provider
#
#   check:
#     1. Check libvirt VMs (kube*, ceph*) — auto-start stopped ones
#     2. Check K3s API reachability + k3s-server service on all nodes
#     3. Check Ceph RGW endpoint health
#     4. Check disk usage on all Ceph svc + OSD nodes
#     5. Check Ceph cluster health and service capacity
#     6. Check K8s workload status in both authentik-bucket-explorer and bucket-explorer
#
# PREREQUISITES:
#   - SSH tunnel to K3s API running, or run: export KUBECONFIG=...
#   - KUBECONFIG set to /tmp/k3s-tunnel-kubeconfig.yaml (default)
#   - Environment overlays in k8s/env/<env>/
#
# NOTE ON CROSS-NAMESPACE ACCESS:
#   configure_authentik_from_cluster() reads backend-secret from bucket-explorer
#   to obtain the OIDC client secret. This requires kubeconfig access to both
#   namespaces, which infra administrators are expected to have.
#   If the webapp has not been deployed yet, skip this step with --skip-config
#   and run: ./infra.sh configure   after the webapp is up.
#
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Namespace managed by this script
INFRA_NAMESPACE="authentik-bucket-explorer"

# Cross-namespace read: configure_authentik reads OIDC secret from the app namespace.
# This is the only reference infra.sh has to the app namespace.
APP_NAMESPACE="bucket-explorer"

INFRA_MANIFESTS_DIR="$SCRIPT_DIR/manifests/infra"
ENV_BASE_DIR="$SCRIPT_DIR/env"

K3S_NODES=("192.168.132.10" "192.168.132.11" "192.168.132.12")
TUNNEL_SOCK="/tmp/k3s-api-tunnel.sock"
TUNNEL_KUBECONFIG="/tmp/k3s-tunnel-kubeconfig.yaml"

ENV_DIR=""
INFRA_SECRETS_FILE=""
AUTHENTIK_SERVICE_FILE=""

# Ceph node groups (used by run_check)
CEPH_SVC_NODES=("192.168.132.80" "192.168.132.81" "192.168.132.82")
CEPH_OSD_NODES=("192.168.132.90" "192.168.132.91" "192.168.132.92")
CEPH_ADMIN="192.168.132.90"
DISK_WARN_THRESHOLD=80
DISK_CRIT_THRESHOLD=95

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

# ==============================================================================
# Rollout helpers
# ==============================================================================

rollout_with_retry() {
    # Wait for a deployment rollout in a given namespace, retrying on timeout.
    # Usage: rollout_with_retry <resource> [timeout] [attempts] [namespace]
    local resource="$1"
    local timeout="${2:-120s}"
    local attempts="${3:-2}"
    local ns="${4:-$INFRA_NAMESPACE}"
    local attempt

    for ((attempt=1; attempt<=attempts; attempt++)); do
        if kubectl rollout status "$resource" -n "$ns" --timeout="$timeout"; then
            return 0
        fi
        warn "$resource rollout attempt ${attempt}/${attempts} failed (ns=$ns)"
        if [ "$attempt" -lt "$attempts" ]; then
            info "Retrying rollout wait for $resource..."
        fi
    done

    fail "$resource rollout did not complete after ${attempts} attempts (ns=$ns)"
}

wait_for_rollout() {
    local resource="$1"
    local timeout="${2:-300s}"
    local attempts="${3:-2}"
    local ns="${4:-$INFRA_NAMESPACE}"
    info "Waiting for $resource (ns=$ns)..."
    rollout_with_retry "$resource" "$timeout" "$attempts" "$ns"
    success "$resource is ready"
}

# ==============================================================================
# Secret + ConfigMap helpers
# ==============================================================================

get_secret_value() {
    # Read a value from a K8s Secret.
    # Usage: get_secret_value <secret-name> <key> <namespace>
    local secret_name="$1"
    local secret_key="$2"
    local ns="$3"
    kubectl get secret "$secret_name" -n "$ns" -o "jsonpath={.data.${secret_key}}" | base64 -d
}

get_config_value() {
    # Read a value from a K8s ConfigMap.
    # Usage: get_config_value <configmap-name> <key> <namespace>
    local config_name="$1"
    local config_key="$2"
    local ns="$3"
    kubectl get configmap "$config_name" -n "$ns" -o "jsonpath={.data.${config_key}}"
}

# ==============================================================================
# Kubeconfig
# ==============================================================================

ensure_kubeconfig() {
    export KUBECONFIG="${KUBECONFIG:-$TUNNEL_KUBECONFIG}"
    if [ ! -f "$KUBECONFIG" ]; then
        error "Kubeconfig not found: $KUBECONFIG"
        echo "  Set up the SSH tunnel to the K3s API first."
        echo "  Quick setup: ssh -L 16443:127.0.0.1:6443 <k3s-node> and scp the kubeconfig."
        exit 1
    fi
    if ! kubectl cluster-info &>/dev/null 2>&1; then
        error "Cannot connect to K3s. SSH tunnel may be down."
        exit 1
    fi
}

# ==============================================================================
# Environment file selection
# ==============================================================================

resolve_infra_environment_files() {
    ENV_DIR="$ENV_BASE_DIR/dev"
    INFRA_SECRETS_FILE="$ENV_DIR/infra-secrets.yaml"

    # Local override (gitignored, contains real credentials)
    local infra_secrets_local="$ENV_DIR/infra-secrets.local.yaml"
    [ -f "$infra_secrets_local" ] && INFRA_SECRETS_FILE="$infra_secrets_local"

    [ -f "$INFRA_SECRETS_FILE" ] || fail "Missing infra secrets file: $INFRA_SECRETS_FILE"

    # NodePort service for local browser access to the Authentik UI
    AUTHENTIK_SERVICE_FILE=""
    local nodeport_local="$ENV_DIR/authentik-service-nodeport.local.yaml"
    local nodeport_default="$ENV_DIR/authentik-service-nodeport.yaml"
    if [ -f "$nodeport_local" ]; then
        AUTHENTIK_SERVICE_FILE="$nodeport_local"
    elif [ -f "$nodeport_default" ]; then
        AUTHENTIK_SERVICE_FILE="$nodeport_default"
    fi

    if [[ "$INFRA_SECRETS_FILE" == *.local.yaml ]]; then
        info "Using local overrides from $ENV_DIR (*.local.*)"
    fi
}

# ==============================================================================
# Apply Authentik infra manifests
# ==============================================================================

apply_infra_manifests_for_env() {
    step "Deploying Authentik infra ($INFRA_NAMESPACE)"

    # Namespace + infra secrets must exist before any Authentik resource is applied
    kubectl apply -f "$INFRA_MANIFESTS_DIR/00-namespace.yaml"
    kubectl apply -f "$INFRA_SECRETS_FILE"

    # Authentik's own databases — both must be ready before the server starts
    kubectl apply -f "$INFRA_MANIFESTS_DIR/01-authentik-postgres.yaml"
    kubectl apply -f "$INFRA_MANIFESTS_DIR/02-authentik-redis.yaml"
    wait_for_rollout "deployment/authentik-postgres" "300s" "2" "$INFRA_NAMESPACE"
    wait_for_rollout "deployment/authentik-redis"    "120s" "2" "$INFRA_NAMESPACE"

    # Authentik server + worker (long startup — up to 5 min on first run due to migrations)
    kubectl apply -f "$INFRA_MANIFESTS_DIR/03-authentik-server.yaml"
    wait_for_rollout "deployment/authentik-server" "300s" "2" "$INFRA_NAMESPACE"
    wait_for_rollout "deployment/authentik-worker" "300s" "2" "$INFRA_NAMESPACE"

    # Dev-only: NodePort to expose Authentik UI on K3s node for local browser access
    if [ -n "$AUTHENTIK_SERVICE_FILE" ]; then
        kubectl apply -f "$AUTHENTIK_SERVICE_FILE"
        success "Applied dev Authentik NodePort service override"
    fi

    success "Authentik infra manifests applied"
}

# ==============================================================================
# Authentik OAuth2 configuration (runs inside the Authentik pod)
# ==============================================================================

configure_authentik_from_cluster() {
    step "Configuring Authentik OAuth2 provider"

    # The Authentik server pod runs in the infra namespace
    local authentik_pod
    authentik_pod=$(kubectl get pods -n "$INFRA_NAMESPACE" -l app=authentik-server \
        --no-headers -o custom-columns=":metadata.name" | head -1)
    [ -z "$authentik_pod" ] && fail "Authentik server pod not found in $INFRA_NAMESPACE"

    # The OIDC client secret lives in the APP namespace (backend-secret).
    # If the app namespace does not exist yet (webapp not deployed), skip configuration
    # and instruct the operator to run this after deploying the webapp.
    if ! kubectl get namespace "$APP_NAMESPACE" &>/dev/null 2>&1; then
        warn "App namespace '$APP_NAMESPACE' not found — skipping OAuth2 configuration."
        warn "Deploy the webapp first, then re-run:  ./infra.sh configure"
        return 0
    fi
    # The Authentik bootstrap password lives in the INFRA namespace (authentik-secret).
    local oidc_secret bootstrap_password oidc_client_id public_app_url
    oidc_secret=$(get_secret_value backend-secret oidc-client-secret "$APP_NAMESPACE")
    bootstrap_password=$(get_secret_value authentik-secret bootstrap-password "$INFRA_NAMESPACE")
    oidc_client_id=$(get_config_value backend-config OIDC_CLIENT_ID "$APP_NAMESPACE")

    public_app_url="${PUBLIC_APP_URL:-http://localhost:3000}"

    # Copy and run the configuration script inside the running Authentik pod
    kubectl cp "$SCRIPT_DIR/configure_authentik.py" \
        "$INFRA_NAMESPACE/$authentik_pod:/tmp/configure_authentik.py"

    kubectl exec -n "$INFRA_NAMESPACE" "$authentik_pod" -- env \
        OIDC_CLIENT_ID="$oidc_client_id" \
        OIDC_CLIENT_SECRET="$oidc_secret" \
        AUTHENTIK_BOOTSTRAP_PASSWORD="$bootstrap_password" \
        PUBLIC_APP_URL="$public_app_url" \
        python /tmp/configure_authentik.py

    # Restart the webapp backend (app namespace) so it picks up the new OAuth2 provider.
    # If the backend deployment does not exist yet (webapp not yet deployed),
    # skip the restart — the backend will pick up the provider on its first start.
    if kubectl get deployment backend -n "$APP_NAMESPACE" &>/dev/null 2>&1; then
        kubectl rollout restart deployment/backend -n "$APP_NAMESPACE"
        kubectl rollout status deployment/backend -n "$APP_NAMESPACE" --timeout=180s
        success "Authentik configured and webapp backend restarted"
    else
        success "Authentik configured — webapp backend will pick up OAuth2 provider on first deploy"
        info "Hint: run  ./app.sh deploy  to deploy the webapp"
    fi
}

# ==============================================================================
# deploy: apply infra manifests + configure Authentik
# ==============================================================================

deploy_infra() {
    local skip_config=false

    while [ $# -gt 0 ]; do
        case "$1" in
            -h|--help)
                echo "Usage: ./infra.sh deploy [--skip-config]"
                echo ""
                echo "  --skip-config   Apply manifests but skip OAuth2 configuration."
                echo "                  Useful when the webapp backend-secret does not exist yet."
                echo "                  Run  ./infra.sh configure  after the webapp is deployed."
                return 0
                ;;
            --skip-config)
                skip_config=true
                ;;
            *) fail "Unknown argument: $1  (run  ./infra.sh deploy --help)" ;;
        esac
        shift
    done

    resolve_infra_environment_files
    ensure_kubeconfig

    step "Infra deploy start"
    apply_infra_manifests_for_env

    if [ "$skip_config" = false ]; then
        configure_authentik_from_cluster
    else
        warn "Skipping OAuth2 configuration (--skip-config). Run  ./infra.sh configure  later."
    fi

    success "Infra deploy completed"
    echo ""
    echo "  Authentik namespace : $INFRA_NAMESPACE"
    echo "  Next step           : ./app.sh deploy [--rebuild]"
    echo ""
}

# ==============================================================================
# configure: re-run OAuth2 configuration only (no manifest re-apply)
# ==============================================================================

run_configure() {
    [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ] && {
        echo "Usage: ./infra.sh configure"; exit 0; }

    resolve_infra_environment_files
    ensure_kubeconfig
    configure_authentik_from_cluster
}

# ==============================================================================
# Status: show Authentik pods
# ==============================================================================

show_status() {
    ensure_kubeconfig

    step "Authentik Pods ($INFRA_NAMESPACE)"
    kubectl get pods -n "$INFRA_NAMESPACE" -o wide 2>/dev/null || \
        warn "Cannot reach K3s or namespace not deployed yet"

    step "SSH Tunnel"
    if [ -S "$TUNNEL_SOCK" ]; then
        success "K3s API tunnel active ($TUNNEL_SOCK)"
    else
        warn "K3s API tunnel NOT running"
    fi

    echo ""
}

# ==============================================================================
# Logs: tail Authentik component logs
# ==============================================================================

show_logs() {
    ensure_kubeconfig
    local component="${1:-authentik-server}"

    case "$component" in
        authentik-server|authentik-worker)
            kubectl logs -l "app=$component" -n "$INFRA_NAMESPACE" --tail=50 -f
            ;;
        *)
            error "Unknown component: $component"
            echo "  Valid options: authentik-server, authentik-worker"
            exit 1
            ;;
    esac
}

# ==============================================================================
# Restart: restart an Authentik deployment without rebuilding
# ==============================================================================

restart_component() {
    local component="$1"
    ensure_kubeconfig

    case "$component" in
        authentik-server|authentik-worker|authentik-postgres|authentik-redis)
            ;;
        *)
            warn "Component '$component' may not be in $INFRA_NAMESPACE — attempting anyway"
            ;;
    esac

    info "Restarting $component (ns=$INFRA_NAMESPACE)..."
    kubectl rollout restart "deployment/$component" -n "$INFRA_NAMESPACE"
    rollout_with_retry "deployment/$component" "180s" "2" "$INFRA_NAMESPACE"
    success "$component restarted"
}

# ==============================================================================
# Cleanup: delete the Authentik namespace
# ==============================================================================

run_cleanup() {
    ensure_kubeconfig

    echo ""
    echo -e "${BOLD}Authentik Infra — Cleanup${NC}"
    echo ""
    info "Deleting namespace '$INFRA_NAMESPACE' and all its resources..."
    kubectl delete namespace "$INFRA_NAMESPACE" --ignore-not-found --timeout=90s 2>/dev/null || true
    success "Namespace $INFRA_NAMESPACE deleted"
    echo ""
    echo "  To verify:  kubectl get ns"
    echo "  To redeploy: ./infra.sh deploy"
    echo ""
}

# ==============================================================================
# Check: full infrastructure health check
# ==============================================================================

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
    $k3s_found || fail_fn "k3s-server not running on any node"

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

    # 6. K8s Workloads — check both namespaces
    echo ""
    echo -e "${BOLD}6. K8s Workloads${NC}"
    local kc="${KUBECONFIG:-$TUNNEL_KUBECONFIG}"
    if [ -f "$kc" ] && KUBECONFIG="$kc" kubectl cluster-info &>/dev/null 2>&1; then
        export KUBECONFIG="$kc"
        for ns in "$INFRA_NAMESPACE" "$APP_NAMESPACE"; do
            local total running
            total=$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | wc -l)
            running=$(kubectl get pods -n "$ns" --no-headers 2>/dev/null | grep -c "Running" || true)
            if [ "$total" -eq 0 ]; then
                warn_fn "$ns: no pods (not deployed yet?)"
            elif [ "$running" -eq "$total" ]; then
                pass_fn "$ns: $running/$total Running"
            else
                warn_fn "$ns: $running/$total Running"
                kubectl get pods -n "$ns" --no-headers 2>/dev/null \
                    | grep -v "Running" | while read -r line; do info_fn "  $line"; done
            fi
        done
    else
        warn_fn "kubectl not connected — cannot check workloads"
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
# Main
# ==============================================================================

usage() {
    echo -e "${BOLD}infra.sh — Authentik Infrastructure Management${NC}"
    echo ""
    echo "  Usage: ./infra.sh <command> [args]"
    echo ""
    echo "  Commands:"
    echo "    deploy [--skip-config]"
    echo "                   Deploy Authentik: namespace, secrets, databases, server"
    echo "                   Then configure the OAuth2 provider for the webapp"
    echo "    configure"
    echo "                   Re-run OAuth2 configuration only (no manifest re-apply)"
    echo "    status         Show pods in $INFRA_NAMESPACE"
    echo "    logs [comp]    Tail logs (authentik-server, authentik-worker)"
    echo "    restart <comp> Restart a deployment without rebuild"
    echo "    cleanup        Delete $INFRA_NAMESPACE namespace"
    echo "    check          Full health check: VMs, K3s, Ceph, disks, K8s workloads"
    echo ""
    echo "  Namespace: $INFRA_NAMESPACE"
    echo ""
    echo "  Environment overlays (k8s/env/dev/):"
    echo "    infra-secrets.yaml     — authentik-secret"
    echo "    Optional local overrides (gitignored): *.local.yaml"
    echo ""
    echo "  Companion script: ./app.sh  (manages the webapp in bucket-explorer namespace)"
    echo ""
}

COMMAND="${1:-}"
shift || true

case "$COMMAND" in
    deploy)
        deploy_infra "$@"
        ;;
    configure)
        run_configure "$@"
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs "${1:-authentik-server}"
        ;;
    restart)
        [ -z "${1:-}" ] && fail "Usage: ./infra.sh restart <component>"
        restart_component "$1"
        ;;
    cleanup)
        run_cleanup
        ;;
    check|preflight)
        run_check
        ;;
    *)
        usage
        exit 1
        ;;
esac

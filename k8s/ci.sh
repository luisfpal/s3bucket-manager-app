#!/bin/bash
# ==============================================================================
# ci.sh — GitHub Actions Runner Controller (ARC) Management
# ==============================================================================
#
# PURPOSE:
#   Installs and manages the GitHub Actions self-hosted runner infrastructure
#   inside the K3s dev cluster using ARC (Actions Runner Controller).
#
#   This gives the CI/CD pipeline a runner that runs INSIDE K3s and has direct
#   in-cluster kubectl access — no SSH tunnel or kubeconfig files needed.
#   It mirrors the same "install via Helm into K3s" pattern used for the
#   HAProxy Ingress Controller.
#
# ARCHITECTURE:
#
#   GitHub push to `dev`
#        │
#        ▼
#   Job 1: Build (GitHub managed runner — ubuntu-latest)
#     - docker build backend, frontend → push to GHCR
#        │
#        ▼  (depends_on: job1)
#   Job 2: Deploy (this self-hosted runner — runs as a Pod in K3s)
#     - kubectl apply manifests
#     - kubectl rollout restart
#
# WHAT ARC DOES:
#   ARC (Actions Runner Controller) is a Kubernetes operator that manages
#   GitHub Actions runners as Pods. When a workflow job is queued:
#   1. ARC detects it via GitHub API polling/webhook
#   2. Spawns a Pod, which registers itself as a GitHub runner
#   3. Pod executes the job, terminates when done
#   4. ARC handles GitHub token rotation (runner tokens expire every 60 min)
#
# RESOURCE REQUIREMENTS:
#   cert-manager (3 pods): ~90m CPU, ~192Mi memory
#   ARC controller:        ~50m CPU, ~128Mi memory
#   Runner pod (idle):     ~100m CPU, ~256Mi memory
#   Runner pod (active):   ~200m CPU, ~512Mi memory
#   TOTAL:                 ~440m CPU, ~1Gi memory (within dev cluster spare capacity)
#
# PREREQUISITES:
#   - KUBECONFIG set (/tmp/k3s-tunnel-kubeconfig.yaml or tunnel active)
#   - helm installed on this host
#   - GITHUB_PAT set to a GitHub classic PAT with `repo` scope
#     (needed by ARC to register the runner with your GitHub repository)
#
# USAGE:
#   export GITHUB_PAT=ghp_your_personal_access_token
#   ./ci.sh install    # Install cert-manager + ARC + create runner
#   ./ci.sh status     # Show runner pods and ARC controller state
#   ./ci.sh uninstall  # Remove ARC and runner (cert-manager stays — it may be shared)
#   ./ci.sh logs       # Tail ARC controller logs
#
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

RUNNER_NAMESPACE="github-runner"
ARC_NAMESPACE="arc-systems"
RUNNER_NAME="bucket-explorer-runner"
GITHUB_REPO="luisfpal/s3bucket-manager-app"

KUBECONFIG="${KUBECONFIG:-/tmp/k3s-tunnel-kubeconfig.yaml}"
export KUBECONFIG

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
fail()    { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }
step()    { echo -e "\n${CYAN}${BOLD}━━━ $* ━━━${NC}"; }

check_prerequisites() {
    if ! command -v helm &>/dev/null; then
        fail "helm is not installed. Install it with: curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash"
    fi
    if ! kubectl cluster-info &>/dev/null; then
        fail "Cannot reach K3s cluster. Check KUBECONFIG and SSH tunnel (./app.sh access)."
    fi
    if [ -z "${GITHUB_PAT:-}" ]; then
        fail "GITHUB_PAT is not set. Export a GitHub classic PAT with 'repo' scope:\n  export GITHUB_PAT=ghp_..."
    fi
}

install_cert_manager() {
    step "Installing cert-manager (ARC dependency)"
    # cert-manager manages TLS certificates for ARC's webhook server.
    helm repo add jetstack https://charts.jetstack.io --force-update
    helm upgrade --install cert-manager jetstack/cert-manager \
        --namespace cert-manager \
        --create-namespace \
        --set installCRDs=true \
        --wait
    success "cert-manager ready"
}

install_arc_controller() {
    step "Installing ARC controller (actions-runner-controller)"
    # ARC is the operator that watches GitHub for queued jobs and spawns runner Pods.
    helm upgrade --install arc \
        oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller \
        --namespace "$ARC_NAMESPACE" \
        --create-namespace \
        --wait
    success "ARC controller ready"
}

install_runner() {
    step "Creating runner namespace and GitHub PAT secret"
    kubectl create namespace "$RUNNER_NAMESPACE" --dry-run=client -o yaml | kubectl apply -f -

    # The PAT lets ARC register the runner with the GitHub repo and renew tokens automatically.
    kubectl create secret generic github-runner-secret \
        --namespace "$RUNNER_NAMESPACE" \
        --from-literal=github_token="${GITHUB_PAT}" \
        --dry-run=client -o yaml | kubectl apply -f -

    step "Applying RBAC (runner ServiceAccount + deployer Role)"
    kubectl apply -f "$SCRIPT_DIR/manifests/ci/rbac.yaml"

    step "Installing RunnerScaleSet ($RUNNER_NAME)"
    # The RunnerScaleSet tells ARC: "for jobs tagged `runs-on: bucket-explorer-runner`,
    # spawn pods using the github-runner-sa ServiceAccount in github-runner namespace."
    # minRunners=1 keeps one pod pre-warmed so the first job doesn't wait for cold start.
    # maxRunners=3 allows parallelism if multiple workflows trigger simultaneously.
    helm upgrade --install "$RUNNER_NAME" \
        oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set \
        --namespace "$RUNNER_NAMESPACE" \
        --set githubConfigUrl="https://github.com/${GITHUB_REPO}" \
        --set githubConfigSecret=github-runner-secret \
        --set minRunners=1 \
        --set maxRunners=3 \
        --set template.spec.serviceAccountName=github-runner-sa
    success "Runner scale set installed"
}

show_status() {
    step "ARC Controller ($ARC_NAMESPACE)"
    kubectl get pods -n "$ARC_NAMESPACE" -o wide 2>/dev/null || warn "ARC not installed"

    step "Runner Pods ($RUNNER_NAMESPACE)"
    kubectl get pods -n "$RUNNER_NAMESPACE" -o wide 2>/dev/null || warn "Runner not installed"

    step "Runner Scale Set"
    kubectl get autoscalingrunnersets -n "$RUNNER_NAMESPACE" 2>/dev/null || true
}

uninstall() {
    step "Uninstalling runner"
    helm uninstall "$RUNNER_NAME" --namespace "$RUNNER_NAMESPACE" 2>/dev/null || true
    step "Uninstalling ARC controller"
    helm uninstall arc --namespace "$ARC_NAMESPACE" 2>/dev/null || true
    step "Cleaning up namespaces"
    kubectl delete namespace "$RUNNER_NAMESPACE" --ignore-not-found
    kubectl delete namespace "$ARC_NAMESPACE" --ignore-not-found
    success "ARC uninstalled (cert-manager preserved)"
}

show_logs() {
    kubectl logs -l app.kubernetes.io/name=gha-runner-scale-set-controller \
        -n "$ARC_NAMESPACE" --tail=50 -f
}

case "${1:-}" in
    install)
        check_prerequisites
        install_cert_manager
        install_arc_controller
        install_runner
        echo ""
        success "CI/CD runner installed!"
        echo ""
        echo "  Runner label for workflow: ${BOLD}bucket-explorer-runner${NC}"
        echo "  Use in workflow:           ${CYAN}runs-on: bucket-explorer-runner${NC}"
        echo ""
        echo "  Verify registration at: https://github.com/${GITHUB_REPO}/settings/actions/runners"
        echo ""
        ;;
    status)
        show_status
        ;;
    uninstall)
        uninstall
        ;;
    logs)
        show_logs
        ;;
    *)
        echo -e "${BOLD}ci.sh — GitHub Actions Runner Controller${NC}"
        echo ""
        echo "  Usage: ./ci.sh <command>"
        echo ""
        echo "  install    Install cert-manager + ARC + runner in K3s"
        echo "  status     Show runner and ARC pod status"
        echo "  uninstall  Remove runner and ARC (cert-manager preserved)"
        echo "  logs       Tail ARC controller logs"
        echo ""
        echo "  Prerequisites:"
        echo "    export GITHUB_PAT=ghp_...   (classic PAT, repo scope)"
        echo "    KUBECONFIG pointing to K3s  (./app.sh access)"
        echo "    helm installed              (curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash)"
        echo ""
        exit 1
        ;;
esac

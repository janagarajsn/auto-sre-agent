#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Prerequisites check — fail immediately before touching anything
# ---------------------------------------------------------------------------
MISSING=()
for cmd in docker kind helm kubectl python3; do
  if ! command -v "$cmd" &>/dev/null; then
    MISSING+=("$cmd")
  fi
done

if [ ${#MISSING[@]} -gt 0 ]; then
  echo "ERROR: The following required tools are not installed:"
  for cmd in "${MISSING[@]}"; do
    echo "  - $cmd"
  done
  echo ""
  echo "Install instructions:"
  echo "  docker:  https://docs.docker.com/get-docker/"
  echo "  kind:    brew install kind          (or https://kind.sigs.k8s.io/docs/user/quick-start/)"
  echo "  helm:    brew install helm          (or https://helm.sh/docs/intro/install/)"
  echo "  kubectl: brew install kubectl       (or https://kubernetes.io/docs/tasks/tools/)"
  echo "  python3: brew install python@3.11"
  echo ""
  echo "Re-run this script after installing the missing tools."
  echo "Any steps already completed will be skipped automatically."
  exit 1
fi

echo "All prerequisites found."
echo ""

# ---------------------------------------------------------------------------
# Kind cluster — idempotent: skip if already exists
# ---------------------------------------------------------------------------
CLUSTER_NAME="sre-agent-dev"

if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
  echo "==> Kind cluster '${CLUSTER_NAME}' already exists, skipping creation"
else
  echo "==> Creating Kind cluster '${CLUSTER_NAME}'"
  kind create cluster --config deploy/kind/cluster.yaml --wait 60s
fi

# ---------------------------------------------------------------------------
# kube-prometheus-stack — helm upgrade --install is idempotent
# ---------------------------------------------------------------------------
echo "==> Installing kube-prometheus-stack"
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm upgrade --install prometheus prometheus-community/kube-prometheus-stack \
  --namespace monitoring --create-namespace \
  --set prometheus.prometheusSpec.retention=2h \
  --set grafana.enabled=true \
  --wait --timeout 5m

echo "==> Applying Prometheus alert rules"
kubectl apply -f deploy/monitoring/prometheus-rules.yaml -n monitoring

# ---------------------------------------------------------------------------
# Redis — idempotent via helm upgrade --install
# ---------------------------------------------------------------------------
echo "==> Installing Redis"
helm repo add bitnami https://charts.bitnami.com/bitnami --force-update
helm upgrade --install redis bitnami/redis \
  --namespace default \
  --set auth.enabled=false \
  --wait --timeout 3m

# ---------------------------------------------------------------------------
# Python dependencies
# ---------------------------------------------------------------------------
echo "==> Installing Python dependencies"
pip install -e ".[dev]"

echo ""
echo "Bootstrap complete."
echo ""
echo "Prometheus:  kubectl port-forward -n monitoring svc/prometheus-operated 9090:9090"
echo "Grafana:     kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80"
echo "Agent:       python -m uvicorn api.main:app --reload"

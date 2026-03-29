# auto-sre-agent

An autonomous AI-powered SRE agent that monitors Kubernetes, detects anomalies, and executes corrective actions — with human-in-the-loop approval for high-risk operations.

## How it works

```
Alert received (Alertmanager webhook or manual trigger)
     │
  detect        ← fetch live metrics, pod logs, k8s events from Prometheus + k8s API
     │
  diagnose      ← LLM root cause analysis (GPT-4o)
     │
  plan          ← LLM selects corrective action
     │
 [approve]      ← human gate for high-risk actions (rollbacks, cordon)
     │
  execute       ← runs the action via Kubernetes API
     │
  observe       ← verifies recovery, saves incident record
```

Graph state is checkpointed in Redis after each node. If the process restarts mid-run, it resumes from the last checkpoint.

## Tech stack

| Component | Technology |
|---|---|
| Agent orchestration | LangGraph + LangChain |
| LLM | OpenAI GPT-4o |
| API | FastAPI |
| State / memory | Redis Stack (RediSearch required) |
| Metrics | Prometheus |
| Visualization | Grafana |
| Infrastructure | Kubernetes (Kind for local dev) |
| Language | Python 3.11+ |

## Project structure

```
auto-sre-agent/
├── agent/
│   ├── core/               # AgentState, router, run_incident() entrypoint
│   ├── nodes/              # detect, diagnose, plan, approve, execute, observe
│   ├── prompts/            # Markdown prompt templates (diagnose.md, plan.md)
│   └── workflows/          # Compiled StateGraph (sre_graph.py)
├── api/
│   ├── middleware/         # Auth (X-API-Key), structured request logging
│   ├── routes/             # alerts, approvals, incidents, health, ui
│   └── schemas/            # Pydantic request/response models
├── tools/
│   ├── kubernetes/         # Pod restart, deployment scale/rollback, events
│   ├── prometheus/         # PromQL client, named metric helpers
│   └── redis/              # Client, distributed locks, incident context store
├── memory/
│   ├── schemas.py          # Domain models: Incident, Diagnosis, ProposedAction, ApprovalRequest
│   ├── short_term.py       # AgentState TypedDict
│   ├── long_term.py        # Redis-backed IncidentStore
│   └── checkpointer.py     # LangGraph Redis checkpointer setup
├── configs/                # Pydantic settings + base/dev/prod YAML overlays
├── observability/          # Structured logging, Prometheus metrics, OpenTelemetry tracing
├── deploy/
│   ├── kind/               # Kind cluster config
│   ├── monitoring/         # PrometheusRule CRDs, Alertmanager config
│   └── helm/               # Helm chart for production deployment
├── scripts/                # bootstrap.sh, simulate_incident.py
└── tests/                  # unit, integration, e2e
```

---

## Setup

### Prerequisites

| Tool | Install |
|---|---|
| Docker | https://docs.docker.com/get-docker/ |
| Kind | `brew install kind` |
| Helm | `brew install helm` |
| kubectl | `brew install kubectl` |
| Python 3.11+ | `brew install python@3.12` |

### 1. Clone and configure

```bash
git clone https://github.com/your-org/auto-sre-agent.git
cd auto-sre-agent
cp .env.example .env
# Edit .env — set OPENAI_API_KEY and API_KEY at minimum
```

### 2. Bootstrap the Kind cluster

```bash
chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh
```

Creates a 3-node Kind cluster and installs `kube-prometheus-stack` and Redis into it. Safe to re-run — cluster creation is idempotent.

### 3. Start the stack

```bash
docker compose up
```

| Service | URL | Notes |
|---|---|---|
| Agent API | http://localhost:8000/docs | Swagger UI |
| Dashboard UI | http://localhost:8000/ui | Approvals + Incidents |
| Grafana | http://localhost:3000 | No login required |
| Prometheus (agent) | http://localhost:9090 | Scrapes agent `/metrics` only |
| Redis | localhost:6379 | Agent state + LangGraph checkpoints |

> `PROMETHEUS_URL` is already set to `http://host.docker.internal:9091` in `docker-compose.yaml`. The agent reads Kubernetes metrics via port-forward (step 4).

### 4. Start port-forwards (keep these running in dedicated terminals)

```bash
# Terminal A — in-cluster Prometheus (Kubernetes metrics)
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-prometheus 9091:9090

# Terminal B — Alertmanager (optional, for inspecting alert routing)
kubectl port-forward -n monitoring svc/prometheus-kube-prometheus-alertmanager 9093:9093
```

> Port-forward in Terminal A must be running before simulating any incident. Without it, the agent gets empty metrics and returns a low-confidence diagnosis.

### 5. Configure Alertmanager to webhook the agent

Patch the in-cluster Alertmanager secret to route alerts to the agent:

```bash
kubectl patch secret alertmanager-prometheus-kube-prometheus-alertmanager -n monitoring \
  --type='merge' \
  -p="$(cat <<'EOF'
{
  "stringData": {
    "alertmanager.yaml": "global:\n  resolve_timeout: 5m\nroute:\n  group_by: ['alertname', 'namespace']\n  group_wait: 30s\n  group_interval: 5m\n  repeat_interval: 4h\n  receiver: 'null'\n  routes:\n  - matchers:\n    - alertname = Watchdog\n    receiver: 'null'\n  - matchers:\n    - alertname =~ \"PodCrashLooping|DeploymentReplicasMismatch|PodOOMKilled|HighCpuUsage|HighMemoryUsage\"\n    receiver: sre-agent\nreceivers:\n- name: 'null'\n- name: sre-agent\n  webhook_configs:\n  - url: 'http://host.docker.internal:8000/alerts/'\n    http_config:\n      authorization:\n        type: ApiKey\n        credentials: change-me\ninhibit_rules:\n- source_matchers: [severity = critical]\n  target_matchers: [severity =~ warning|info]\n  equal: [namespace, alertname]\n"
  }
}
EOF
)"
```

> `host.docker.internal:8000` is how the Kind cluster reaches the agent running in Docker Compose on your Mac.

---

## Simulating incidents

Always flush Redis before each demo to clear deduplication locks from previous runs:

```bash
docker exec auto-sre-agent-redis-1 redis-cli FLUSHALL
```

### Scenario 1 — PodCrashLooping (auto-remediation, no approval)

Deploy a pod that crashes immediately:

```bash
kubectl run crash-app --image=busybox --restart=Always -n default -- sh -c "exit 1"
```

**Option A — Wait for Alertmanager (fully automatic)**

The `PodCrashLooping` rule has `for: 0m` so the alert fires as soon as the pod enters `CrashLoopBackOff` (~30–60s). Alertmanager routes it to the agent within another 30s (group_wait).

**Option B — Trigger manually right now (no Alertmanager wait)**

```bash
# Via the API (agent must be running via docker compose)
curl -X POST http://localhost:8000/alerts/test \
  -H "X-API-Key: change-me" -H "Content-Type: application/json" \
  -d '{"alert_name": "PodCrashLooping", "severity": "high", "namespace": "default", "labels": {"pod": "crash-app"}}'

# OR directly from terminal (bypasses the API entirely)
source venv/bin/activate
python scripts/simulate_incident.py --alert PodCrashLooping --namespace default --pod crash-app
```

Watch the agent work:
```bash
docker compose logs -f agent
```

The agent will detect, diagnose, plan `restart_pod`, and execute it automatically — no approval needed in dev mode.

Clean up after the demo:
```bash
kubectl delete pod crash-app -n default
```

### Scenario 2 — DeploymentReplicasMismatch (HITL rollback, requires approval)

**Step 1** — Deploy the web-app (skip if already running):
```bash
kubectl create deployment web-app --image=nginx:alpine --replicas=1 -n default
```

Wait until it's running:
```bash
kubectl rollout status deployment/web-app -n default
```

**Step 2** — Break it with a bad image update:
```bash
kubectl patch deployment web-app -n default --type='json' \
  -p='[
    {"op":"replace","path":"/spec/template/spec/containers/0/image","value":"busybox"},
    {"op":"add","path":"/spec/template/spec/containers/0/command","value":["sh","-c","exit 1"]}
  ]'
```

**Step 3** — Trigger the agent.

**Option A — Wait for Alertmanager (fully automatic)**

The `DeploymentReplicasMismatch` rule has `for: 5m`, so the alert fires after 5 minutes of replica mismatch. Watch it at `http://localhost:9091/alerts`.

**Option B — Trigger manually right now (no 5-minute wait)**

```bash
# Via the API (agent must be running via docker compose)
curl -X POST http://localhost:8000/alerts/test \
  -H "X-API-Key: change-me" -H "Content-Type: application/json" \
  -d '{"alert_name": "DeploymentReplicasMismatch", "severity": "high", "namespace": "default", "labels": {"deployment": "web-app"}}'

# OR directly from terminal
source venv/bin/activate
python scripts/simulate_incident.py --alert DeploymentReplicasMismatch --namespace default --pod web-app
```

> For the manual trigger to produce a confident diagnosis, the broken deployment must already be in place (Step 2) so Prometheus has replica mismatch metrics to return.

**Step 4** — The agent will detect the mismatch, diagnose it as a bad deployment update, and plan a `rollback_deployment`. This requires approval. Open the dashboard:

```
http://localhost:8000/ui
```

The **Pending Approvals** tab shows the approval card with action details, risk level, and rationale. Click **Approve** to let the agent execute the rollback.

**Step 5** — After approval, the agent rolls back to the previous ReplicaSet (nginx:alpine). Verify:
```bash
kubectl rollout status deployment/web-app -n default
kubectl get pods -n default
```

### Manual trigger (no Alertmanager needed)

```bash
source venv/bin/activate
python scripts/simulate_incident.py --alert PodCrashLooping --namespace default
```

---

## Human-in-the-loop approvals

High-risk actions (rollbacks, node cordons) suspend the LangGraph graph and wait for a human decision.

**Dashboard UI (recommended):**
```
http://localhost:8000/ui
```

**Via curl:**
```bash
# List pending approvals
curl http://localhost:8000/approvals/pending -H "X-API-Key: change-me"

# Submit a decision
curl -X POST http://localhost:8000/approvals/{approval_id} \
  -H "X-API-Key: change-me" -H "Content-Type: application/json" \
  -d '{"approved": true, "reviewer": "alice", "notes": "Confirmed safe to rollback"}'
```

Approvals expire after 15 minutes (`approval_timeout_seconds: 900` in `configs/base.yaml`).

---

## API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/readyz` | Readiness probe (checks Redis) |
| `POST` | `/alerts/` | Alertmanager webhook receiver |
| `POST` | `/alerts/test` | Synchronous test trigger |
| `GET` | `/incidents/` | List recent incidents |
| `GET` | `/incidents/{id}` | Get incident by ID |
| `GET` | `/approvals/pending` | List approvals waiting for decision |
| `GET` | `/approvals/{id}` | Get approval request status |
| `POST` | `/approvals/{id}` | Submit approval decision |
| `GET` | `/ui` | Dashboard (Approvals + Incidents) |
| `GET` | `/metrics` | Prometheus metrics (agent-side) |
| `GET` | `/docs` | Swagger UI |

All endpoints except `/healthz` and `/readyz` require `X-API-Key` header.

---

## Common errors

**`Invalid kube-config file` / `Network is unreachable host.docker.internal:<port>`**

The Kind cluster was recreated and its API server port changed. Get the new port and update `docker-compose.yaml`:

```bash
kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}' | grep -o ':[0-9]*' | tr -d ':'
```

Update `K8S_SERVER_OVERRIDE` in `docker-compose.yaml` to match, then rebuild:
```bash
docker compose up -d --build agent
```

> Never run `kubectl config set-cluster ... --server=...` — this patches your local kubeconfig and breaks `kubectl` in your terminal.

---

**Diagnosis returns `confidence: 0.4` with empty metrics**

The port-forward to the in-cluster Prometheus (Terminal A, port 9091) is not running. Start it and retry.

---

**Alert fires in Prometheus but no POST to `/alerts/`**

Two common causes:
1. Alertmanager `repeat_interval` (4h) — the alert group was already sent and won't resend for 4 hours. Flush Redis and delete/recreate the pod to generate a new alert fingerprint.
2. Alertmanager hasn't loaded the patched secret — verify with `http://localhost:9093` → Status.

---

**Agent goes NOOP for DeploymentReplicasMismatch**

The Prometheus port-forward (9091) was not running during the detect phase — empty metrics → low confidence → NOOP. Flush Redis and retrigger.

---

**`PodCrashLooping` stuck on `pending` in Prometheus**

You are looking at the built-in `KubePodCrashLooping` rule which has `for: 15m`. Use the custom `PodCrashLooping` rule (from `deploy/monitoring/prometheus-rules.yaml`) which has `for: 0m`.

---

**Alertmanager stops sending after a 401**

Alertmanager treats 4xx as unrecoverable and stops retrying permanently. After fixing auth, restart Alertmanager and recreate the pod:
```bash
kubectl rollout restart statefulset/alertmanager-prometheus-kube-prometheus-alertmanager -n monitoring
```

---

**`unknown command 'FT._LIST'` from Redis**

Plain `redis:alpine` is running instead of Redis Stack. The LangGraph checkpointer requires the RediSearch module. Ensure `docker-compose.yaml` uses `redis/redis-stack-server:latest`.

---

**Deduplication: second incident not triggered even after first is resolved**

Redis still holds the dedup lock (`sre:incident_lock:{alert_name}:{namespace}`). Flush Redis before each demo:
```bash
docker exec auto-sre-agent-redis-1 redis-cli FLUSHALL
```

# auto-sre-agent

An autonomous AI-powered Site Reliability Engineering agent that monitors Kubernetes infrastructure, detects anomalies, and executes corrective actions — with human-in-the-loop approval for high-risk operations.

## How it works

When an alert fires (via Alertmanager or manual trigger), the agent runs a LangGraph pipeline:

```
Alert received
     │
  detect        ← fetch live metrics, pod logs, k8s events
     │
  diagnose      ← LLM root cause analysis
     │
  plan          ← LLM selects corrective action
     │
 [approve]      ← human gate (high-risk actions only)
     │
  execute       ← runs the action via Kubernetes API
     │
  observe       ← verifies recovery, saves incident record
```

Graph state is checkpointed in Redis after each node, enabling fault-tolerant execution and resumption after human approval.

## Tech stack

| Component | Technology |
|---|---|
| Agent orchestration | LangGraph + LangChain |
| LLM | OpenAI GPT-4o |
| API | FastAPI |
| State / memory | Redis Stack (requires RediSearch module) |
| Metrics source | Prometheus |
| Visualization | Grafana |
| Infrastructure | Kubernetes (Kind for local dev) |
| Deployment | Helm + Kustomize |
| Language | Python 3.11+ |

## Project structure

```
auto-sre-agent/
├── agent/                  # LangGraph nodes, graph assembly, prompts
│   ├── core/               # AgentState, router, entrypoint
│   ├── nodes/              # detect, diagnose, plan, approve, execute, observe
│   ├── prompts/            # Markdown prompt templates
│   └── workflows/          # Compiled StateGraph + subgraph stubs
├── tools/                  # External integrations (BaseTool registry)
│   ├── prometheus/         # PromQL client + named metric helpers
│   ├── kubernetes/         # Pod restart, deployment scale/rollback, events
│   ├── redis/              # Client, distributed locks, context store
│   └── future/             # AWS / GCP stubs
├── memory/                 # Schemas, AgentState, Redis incident store, checkpointer
├── api/                    # FastAPI: webhook receiver, approvals, incidents, health
├── configs/                # Pydantic settings + per-environment YAML overlays
├── observability/          # Structured logging, Prometheus metrics, OpenTelemetry
│   └── provisioning/       # Grafana auto-provisioned datasources and dashboards
├── deploy/                 # Helm chart, Kustomize overlays, Kind config, alert rules
├── tests/                  # Unit, integration, e2e
└── scripts/                # Local dev: bootstrap, simulate incident, seed metrics
```

## Quickstart (local)

### Prerequisites

The bootstrap script checks for these before doing anything — install all before running it:

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

### 2. Set up Python environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

> **Note:** If you have a Homebrew Python alias in your shell (`python` aliased to `/opt/homebrew/...`), it overrides venv activation. Run `unalias python` in your session, or always use `venv/bin/python` explicitly.

### 3. Bootstrap the Kind cluster

```bash
chmod +x scripts/bootstrap.sh
./scripts/bootstrap.sh
```

This creates a 3-node Kind cluster and installs `kube-prometheus-stack` and Redis into it. Safe to re-run — the cluster creation step is idempotent.

### 4. Start the local stack

Run this in a dedicated terminal (keep it running):

```bash
docker-compose up
```

Services started by Docker Compose:

| Service | URL | Purpose |
|---|---|---|
| Agent API | http://localhost:8000/docs | FastAPI — use `/docs` not `/` (no root route) |
| Grafana | http://localhost:3000 | Dashboards |
| Prometheus (agent) | http://localhost:9090 | Scrapes agent `/metrics` only |
| Redis Stack | localhost:6379 | Agent state + LangGraph checkpointing |

> **Important:** The Docker Compose Prometheus on port 9090 only scrapes the agent itself. It does **not** have Kubernetes metrics. See [Grafana setup](#grafana-and-kubernetes-metrics) below.

### 5. Point the agent at the in-cluster Prometheus

By default `.env` has `PROMETHEUS_URL=http://localhost:9090` which points at the Docker Compose Prometheus (agent metrics only). For the agent to see real Kubernetes metrics during incident diagnosis, update `.env`:

```
PROMETHEUS_URL=http://host.docker.internal:9091
```

Then keep this port-forward running in a dedicated terminal before simulating incidents:

```bash
kubectl port-forward -n monitoring svc/prometheus-operated 9091:9090
```

Rebuild and restart the agent after changing `.env`:

```bash
docker-compose up -d --build agent
```

### 6. Simulate an incident

Deploy a real crashlooping pod so the agent has actual metrics and logs to reason about:

```bash
kubectl create deployment crash-app --image=busybox -- sh -c "echo 'OOM error'; exit 1"

# Wait until you see CrashLoopBackOff with a few restarts
kubectl get po -w
```

Open a second terminal, activate the venv, and run:

```bash
source venv/bin/activate
python scripts/simulate_incident.py --alert PodCrashLooping --namespace default
```

Expected output:
```
Incident completed:
  ID:     <uuid>
  Status: resolved
  Root cause: Pod crash-app is crash looping. Logs show OOM error...
  Action: restart_pod on crash-app
  Result: success
```

> Without a real crashlooping pod, the agent will still run but return a low-confidence diagnosis (`confidence: 0.4`) because Prometheus has no metrics to return.

Or send directly to the API:

```bash
curl -X POST http://localhost:8000/alerts/test \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "PodCrashLooping",
    "severity": "high",
    "namespace": "default",
    "labels": {"pod": "crash-app"}
  }'
```

## Grafana and Kubernetes metrics

There are **two separate Prometheus instances** in the local setup:

| Instance | Where | Scrapes |
|---|---|---|
| Docker Compose Prometheus | `localhost:9090` | Agent `/metrics` only |
| In-cluster Prometheus | Kind cluster | All Kubernetes metrics (pods, nodes, deployments) |

To see Kubernetes metrics in Grafana, you need to point it at the in-cluster Prometheus.

**Step 1** — Port-forward the in-cluster Prometheus to a different port (keep this running in its own terminal):

```bash
kubectl port-forward -n monitoring svc/prometheus-operated 9091:9090
```

Use port `9091` to avoid conflicting with the Docker Compose Prometheus already on `9090`.

**Step 2** — Add a second datasource in Grafana:

1. Go to `http://localhost:3000` → Connections → Data Sources → Add new
2. Type: Prometheus
3. URL: `http://host.docker.internal:9091`
4. Save & Test — should show green

**Step 3** — Import a Kubernetes dashboard:

1. Dashboards → Import → enter ID `15661` → Load
2. Select the `host.docker.internal:9091` datasource
3. Import — panels will populate immediately

### Persisting dashboards

Dashboards saved only in the Grafana UI are stored in the `grafana_data` Docker volume and will be lost on `docker-compose down -v`. To persist them:

1. In Grafana: Dashboard → Share → Export → Save to file
2. Drop the JSON into `observability/provisioning/dashboards/`
3. Grafana auto-provisions it on next restart

> Use `docker-compose down` (without `-v`) for day-to-day restarts to preserve volumes. Only use `-v` when you need a clean slate.

## Terminal layout (local dev)

Running the full local stack requires several long-lived processes. Recommended layout:

| Terminal | Command | Keep alive? |
|---|---|---|
| 1 | `docker-compose up` | Yes |
| 2 | `kubectl port-forward -n monitoring svc/prometheus-operated 9091:9090` | Yes |
| 3 | `kubectl get po -w` | Optional |
| 4 | `source venv/bin/activate && python scripts/simulate_incident.py ...` | One-off |

> The port-forward in Terminal 2 must be running before you trigger any incident — the agent queries the in-cluster Prometheus at `host.docker.internal:9091` during the detect phase.

## Configuration

Settings load in this order — **last wins**:

```
configs/base.yaml → configs/{ENV}.yaml → environment variables
```

Environment variables (set by `docker-compose environment:` block or Kubernetes) always take highest precedence. The `.env` file is for local dev only and is excluded from the Docker image via `.dockerignore`.

Set `ENV=dev`, `ENV=staging`, or `ENV=prod` to switch overlays. All available variables are documented in [.env.example](.env.example).

## Human-in-the-loop approvals

High-risk actions (rollbacks, node cordons) suspend the graph and wait for a human decision.

**Check pending approvals:**
```bash
curl http://localhost:8000/incidents/pending-approvals \
  -H "X-API-Key: change-me"
```

**Submit a decision:**
```bash
curl -X POST http://localhost:8000/approvals/{approval_id} \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{"approved": true, "reviewer": "alice", "notes": "Confirmed safe to rollback"}'
```

The graph resumes automatically and executes (or skips) the action based on the decision.

## Alertmanager integration (auto-trigger)

Wire up Alertmanager so the agent kicks off automatically when an alert fires — no manual simulation needed.

**Step 1** — Patch the in-cluster Alertmanager config to webhook the agent:

```bash
kubectl -n monitoring create secret generic alertmanager-prometheus-kube-prometheus-alertmanager \
  --from-literal=alertmanager.yaml='
global:
  resolve_timeout: 5m
route:
  group_by: ["alertname", "namespace"]
  group_wait: 10s
  group_interval: 1m
  repeat_interval: 12h
  receiver: sre-agent
receivers:
  - name: sre-agent
    webhook_configs:
      - url: "http://host.docker.internal:8000/alerts/"
        http_config:
          authorization:
            type: ApiKey
            credentials: change-me
        send_resolved: false
' --dry-run=client -o yaml | kubectl apply -f -
```

**Step 2** — Restart Alertmanager to pick up the new config:

```bash
kubectl rollout restart statefulset/alertmanager-prometheus-kube-prometheus-alertmanager -n monitoring
```

**Step 3** — Verify by opening `http://localhost:9091/alerts` in your browser. Once `crash-app` has been running for 2+ minutes, `PodCrashLooping` will appear as firing and Alertmanager will POST to the agent automatically.

> `host.docker.internal:8000` is how the Kind cluster reaches the agent running in Docker Compose on your Mac. On Linux, replace with your host IP.

For production Kubernetes deployments, deploy the agent inside the cluster and use the service DNS name instead:

```yaml
url: "http://auto-sre-agent.sre-agent.svc.cluster.local:8000/alerts/"
```

## Adding a new tool

1. Create `tools/your_integration/your_tool.py` extending `BaseTool`
2. Implement `async def run(self, **kwargs) -> ToolResult`
3. Register it in `tools/base.py` → `register_all_tools()`
4. Call it from the relevant node via `ToolRegistry.get("your_tool_name")`

```python
from tools.base import BaseTool, ToolResult

class MyNewTool(BaseTool):
    name = "my_tool"
    description = "Does something useful"

    async def run(self, **kwargs) -> ToolResult:
        return ToolResult.ok({"result": "done"})
```

## Running tests

```bash
# Unit + integration
pytest tests/unit tests/integration -v

# With coverage
pytest tests/unit tests/integration --cov --cov-report=term-missing

# End-to-end (requires running Kind cluster + real OpenAI key)
RUN_E2E=1 pytest tests/e2e -v --no-cov
```

## Deployment

### Helm (recommended)

```bash
kubectl create secret generic auto-sre-agent-secrets \
  --from-literal=OPENAI_API_KEY=sk-... \
  --from-literal=API_KEY=your-api-key \
  --from-literal=REDIS_URL=redis://redis-master:6379/0

helm upgrade --install auto-sre-agent deploy/helm/auto-sre-agent \
  --namespace sre-agent --create-namespace \
  --values deploy/helm/auto-sre-agent/values.yaml
```

### Kustomize (GitOps)

```bash
kubectl apply -k deploy/k8s/overlays/prod
```

## Troubleshooting

**`/incidents/` returns Internal Server Error**
The agent container is not reaching Redis. Check:
```bash
docker-compose exec agent env | grep -i redis   # should show redis://redis:6379/0
docker-compose logs agent --tail=30             # look for ConnectionRefusedError
```
If `REDIS_URL` shows `localhost:6379`, rebuild the image (`.env` may have been baked in):
```bash
docker-compose build --no-cache agent && docker-compose up -d
```

**Diagnosis returns `confidence: 0.4` with empty metrics**
The agent can't find metrics for the pod. Two common causes:
1. `PROMETHEUS_URL` in `.env` still points to `localhost:9090` (Docker Compose Prometheus) instead of `host.docker.internal:9091` (in-cluster Prometheus)
2. The port-forward to the in-cluster Prometheus is not running — start it in a dedicated terminal

**Grafana shows "no such host" for Prometheus datasource**
Containers are not on the same Docker network. Ensure `docker-compose.yaml` has an explicit `networks: sre:` block and all services reference it. Then:
```bash
docker-compose down && docker-compose up -d
```

**Grafana dashboard 15661 shows no data**
The datasource variable at the top of the dashboard is still set to the old Docker Compose Prometheus. Click the datasource dropdown and switch it to `host.docker.internal:9091`.

**`ModuleNotFoundError: No module named 'pydantic'` when running scripts**
The venv is not active or packages were installed in a different environment:
```bash
source venv/bin/activate
pip show pydantic  # should show Location: .../venv/lib/...
```
If `which python` shows `/opt/homebrew/...` instead of `venv/bin/python`, you have a shell alias overriding venv activation:
```bash
unalias python
```

**`unknown command 'FT._LIST'` from Redis**
You are using plain `redis:alpine` instead of Redis Stack. The LangGraph checkpointer requires the RediSearch module. Ensure `docker-compose.yaml` uses `redis/redis-stack-server:latest`.

**Simulated pod terminates after agent restart action**
Expected if the pod was created with `kubectl run` (bare pod — no controller). Use `kubectl create deployment` instead so the Deployment controller recreates the pod after deletion.

## API reference

| Method | Path | Description |
|---|---|---|
| `GET` | `/healthz` | Liveness probe |
| `GET` | `/readyz` | Readiness probe (checks Redis) |
| `POST` | `/alerts/` | Alertmanager webhook receiver |
| `POST` | `/alerts/test` | Synchronous test trigger |
| `GET` | `/incidents/` | List recent incidents |
| `GET` | `/incidents/{id}` | Get incident by ID |
| `GET` | `/incidents/pending-approvals` | List incidents awaiting approval |
| `GET` | `/approvals/{id}` | Get approval request status |
| `POST` | `/approvals/{id}` | Submit approval decision |
| `GET` | `/metrics` | Prometheus metrics (agent-side) |
| `GET` | `/docs` | Interactive API docs (Swagger UI) |

All endpoints except `/healthz` and `/readyz` require `X-API-Key` header.

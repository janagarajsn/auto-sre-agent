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

### 5. Simulate an incident

Open a second terminal, activate the venv, and run:

```bash
source venv/bin/activate
python scripts/simulate_incident.py --alert PodCrashLooping --namespace default
```

Or send directly to the API:

```bash
curl -X POST http://localhost:8000/alerts/test \
  -H "X-API-Key: change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "alert_name": "PodCrashLooping",
    "severity": "high",
    "namespace": "default",
    "labels": {"pod": "my-app-abc123"}
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

## Configuration

Settings are loaded in this order (highest precedence last):

```
configs/base.yaml → configs/{ENV}.yaml → environment variables (.env)
```

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

## Alertmanager integration

Point Alertmanager at the agent webhook:

```yaml
# alertmanager.yml
receivers:
  - name: sre-agent
    webhook_configs:
      - url: http://auto-sre-agent:8000/alerts/
        http_config:
          authorization:
            type: ApiKey
            credentials: your-api-key
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

# Codebase Walkthrough — auto-sre-agent

A top-to-bottom walkthrough of how the code is structured and why. Each section builds on the previous one.

---

## Table of Contents

1. [The Big Picture](#1-the-big-picture)
2. [Entry Point — how an incident starts](#2-entry-point)
3. [The State — shared whiteboard](#3-the-state)
4. [The Graph — wiring nodes together](#4-the-graph)
5. [The Router — conditional edges](#5-the-router)
6. [Node 1: detect](#6-node-1-detect)
7. [Node 2: diagnose](#7-node-2-diagnose)
8. [Node 3: plan](#8-node-3-plan)
9. [Node 4: approve — human-in-the-loop gate](#9-node-4-approve)
10. [Node 5: execute](#10-node-5-execute)
11. [Node 6: observe](#11-node-6-observe)
12. [Tools — talking to the outside world](#12-tools)
13. [Memory — short-term vs long-term](#13-memory)
14. [Checkpointing — how graph resumption works](#14-checkpointing)
15. [The API layer](#15-the-api-layer)
16. [Configuration](#16-configuration)
17. [Observability](#17-observability)
18. [End-to-end data flow](#18-end-to-end-data-flow)

---

## 1. The Big Picture

The agent is a **LangGraph state machine** wrapped in a **FastAPI service**, backed by **Redis** for memory, talking to **Prometheus** for metrics and **Kubernetes** for actions.

```
Alertmanager (or simulate_incident.py)
        │
        ▼
  FastAPI /alerts/              api/routes/alerts.py
        │
        ▼
  run_incident()                agent/core/agent.py
        │
        ▼
  LangGraph pipeline
  ┌─────────────────────────────────────────────────────────────┐
  │  detect → diagnose → plan → [approve] → execute → observe   │
  └─────────────────────────────────────────────────────────────┘
        │              │              │
   Prometheus      OpenAI LLM    Kubernetes API
        │
        ▼
  Redis (incident store + LangGraph checkpointer)
```

Two things flow through the system:
- **AgentState** — in-memory whiteboard shared across all nodes during a run
- **Incident** — the persisted record in Redis that survives restarts

---

## 2. Entry Point

**File:** `agent/core/agent.py` → `run_incident()`

This is the first function called when an alert arrives, from either the API webhook or `simulate_incident.py`.

```python
async def run_incident(alert: AlertSignal) -> Incident:
    # Deduplication — Alertmanager re-sends every repeat_interval.
    # One active incident per (alert_name, namespace) at a time.
    acquired = await redis.set(lock_key, "1", nx=True, ex=1800)
    if not acquired:
        return Incident(...)   # silently skip duplicate

    incident_id = uuid.uuid4()   # permanent ID for this business event
    thread_id = str(uuid.uuid4())  # LangGraph's handle for checkpointing

    incident = Incident(id=incident_id, alert=alert, thread_id=thread_id)
    await store.save(incident)   # saved immediately — record exists even if graph crashes

    async with build_checkpointer() as checkpointer:
        graph = build_sre_graph(checkpointer=checkpointer)
        async for event in graph.astream(initial_state, config=config):
            ...  # each event = one node completed
```

**Why two UUIDs?**
`incident_id` identifies the business event. `thread_id` identifies the LangGraph execution. They are separate so that in future one incident could spawn multiple graph runs (e.g. retry after failure).

The deduplication lock (`sre:incident_lock:{alert_name}:{namespace}`) is released once the incident reaches a terminal state (RESOLVED or FAILED), so a recurrence can be handled.

---

## 3. The State

**File:** `memory/short_term.py`

`AgentState` is a `TypedDict` — a Python dict with typed keys. LangGraph passes it between nodes. Each node receives the full state and returns only the fields it changed (a delta). LangGraph merges the delta back.

```python
class AgentState(TypedDict):
    incident_id: UUID           # never changes after creation
    thread_id: str
    alert: AlertSignal          # the input — never changes

    diagnosis: DiagnosisResult | None       # set by diagnose node
    proposed_action: ProposedAction | None  # set by plan node
    approval_request: ApprovalRequest | None  # set by approve node
    action_result: ActionResult | None      # set by execute node

    status: IncidentStatus      # updated by each node
    requires_approval: bool     # set by plan node
    error: str | None

    messages: Annotated[list[Any], add_messages]  # append-only (LLM history)
    raw_metrics: dict[str, Any]   # fetched by detect, consumed by diagnose
    raw_logs: list[str]
    raw_k8s_events: list[...]
    incident: Incident            # the full persisted record
```

**`messages` is special.** `Annotated[list, add_messages]` tells LangGraph to use the `add_messages` reducer — appending to the list instead of replacing it. This is how LLM conversation history accumulates across nodes without any node overwriting the previous messages.

**`raw_metrics`, `raw_logs`, `raw_k8s_events`** are scratchpad data — fetched by detect, consumed by diagnose, and never persisted to the long-term Incident record. Separating them makes the data flow explicit.

---

## 4. The Graph

**File:** `agent/workflows/sre_graph.py`

```python
def build_sre_graph(checkpointer=None):
    graph = StateGraph(AgentState)

    graph.add_node("detect", detect_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("plan", plan_node)
    graph.add_node("approve", approve_node)
    graph.add_node("execute", execute_node)
    graph.add_node("observe", observe_node)

    graph.add_edge(START, "detect")

    graph.add_conditional_edges("detect",   route_after_detect,   {...})
    graph.add_conditional_edges("diagnose", route_after_diagnose, {...})
    graph.add_conditional_edges("plan",     route_after_plan,     {...})
    graph.add_conditional_edges("approve",  route_after_approve,  {...})
    graph.add_conditional_edges("execute",  route_after_execute,  {...})

    graph.add_edge("observe", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["approve"],   # pause HERE for human approval
    )
```

**`interrupt_before=["approve"]`** — when the graph reaches the `approve` node, it pauses *before* entering it, saves the full graph state to Redis via the checkpointer, and stops. The graph is frozen in time, waiting for a human decision. `resume_incident()` is called later to unfreeze it.

**`add_conditional_edges`** — after a node completes, calls the router function with the current state. The router returns a string that maps to the next node name.

---

## 5. The Router

**File:** `agent/core/router.py`

Each function inspects state and returns the name of the next node.

```python
def route_after_diagnose(state: AgentState) -> str:
    diagnosis = state.get("diagnosis")
    if diagnosis is None or diagnosis.confidence < 0.5:
        return "observe"   # not enough confidence — exit gracefully
    return "plan"

def route_after_plan(state: AgentState) -> str:
    action = state.get("proposed_action")
    if action is None:
        return "observe"
    if action.requires_approval:
        return "approve"   # high risk — human must decide
    return "execute"       # low risk — auto-execute

def route_after_approve(state: AgentState) -> str:
    approval = state.get("approval_request")
    if approval and approval.approved is True:
        return "execute"
    return "observe"       # rejected — skip execution
```

Every router has a default path to `observe`. If anything is missing, unclear, or rejected, the graph exits gracefully rather than crashing or getting stuck.

---

## 6. Node 1: detect

**File:** `agent/nodes/detect.py`

**Input:** `alert`
**Output:** `raw_metrics`, `raw_logs`, `raw_k8s_events`

```python
async def detect_node(state: AgentState) -> dict:
    namespace = state["alert"].namespace

    # All queries run in parallel
    (cpu, mem, restarts, error_rate, events, pods) = await asyncio.gather(
        get_cpu_usage(namespace),
        get_memory_usage(namespace),
        get_pod_restart_count(namespace),
        get_http_error_rate(namespace),
        list_recent_events(namespace, event_type="Warning"),
        list_pods(namespace),
        return_exceptions=True,   # one failing tool doesn't abort detection
    )

    # Collect logs for high-restart pods
    for pod in pods:
        if pod.get("restarts", 0) >= 3:
            log = await get_pod_logs(namespace, pod["name"], tail_lines=50)
            logs.append(log)

    return {"raw_metrics": {...}, "raw_logs": logs, "raw_k8s_events": events}
```

`return_exceptions=True` in `asyncio.gather` means if Prometheus is unreachable, the exception is returned as a value rather than raised. The node substitutes an empty list and continues. One tool failure does not abort detection.

Running all queries concurrently means total time equals the slowest single query, not the sum.

---

## 7. Node 2: diagnose

**File:** `agent/nodes/diagnose.py`
**Prompt:** `agent/prompts/diagnose.md`

**Input:** `raw_metrics`, `raw_logs`, `raw_k8s_events`, `alert`
**Output:** `diagnosis` (DiagnosisResult)

```python
async def diagnose_node(state: AgentState) -> dict:
    llm = ChatOpenAI(model=settings.openai_model, temperature=0.0)
    system_prompt = load_prompt("diagnose")   # reads agent/prompts/diagnose.md
    user_content = _build_context(state)      # formats all raw data into structured text

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ])

    diagnosis = _parse_diagnosis(response.content)
    return {"diagnosis": diagnosis, "messages": [...]}
```

`temperature=0.0` means deterministic output — for diagnosis you want the most likely answer, not variation.

`_build_context()` assembles metrics, logs, and events into a structured text block that the LLM sees:
```
## Alert
Name: PodCrashLooping, Severity: high, Namespace: default

## Kubernetes Events
[{"reason": "BackOff", "message": "Back-off restarting failed container"}, ...]

## Pod Restart Counts
[{"metric": {"pod": "crash-app"}, "value": "5"}, ...]

## Recent Logs
=== crash-app ===
OOM error
```

`_parse_diagnosis()` looks for a ` ```json ... ``` ` block in the response. If the LLM returns well-formed JSON it deserialises it into `DiagnosisResult`. If not, it falls back to `confidence=0.5` so the run continues rather than crashing.

Prompts live in `agent/prompts/*.md` as Markdown files, not hardcoded strings. You can edit prompt instructions without touching Python. `load_prompt()` reads and caches with `@lru_cache`.

---

## 8. Node 3: plan

**File:** `agent/nodes/plan.py`
**Prompt:** `agent/prompts/plan.md`

**Input:** `diagnosis`, `alert`
**Output:** `proposed_action` (ProposedAction)

```python
async def plan_node(state: AgentState) -> dict:
    response = await llm.ainvoke([
        SystemMessage(content=load_prompt("plan")),
        HumanMessage(content=f"## Diagnosis\n{diagnosis.root_cause}\n\n## Alert\n..."),
    ])

    proposed = _parse_action(response.content, alert.namespace)
    proposed.requires_approval = _needs_approval(proposed, settings.is_production)
    ...
```

`_needs_approval()` applies hard business rules on top of whatever the LLM said:

```python
def _needs_approval(action, is_prod):
    if action.action_type in {ActionType.ROLLBACK_DEPLOYMENT, ActionType.CORDON_NODE}:
        return True   # always — too destructive
    if action.action_type == ActionType.RESTART_POD and not is_prod:
        return False  # auto-approve in dev
    return action.risk_level in (Severity.HIGH, Severity.CRITICAL)
```

The LLM proposes `requires_approval` in its JSON, but this function **overrides it** with hard rules. The LLM does not get to decide whether a rollback needs approval — the code does. This is a deliberate safety boundary.

If the LLM response cannot be parsed, the node returns a NOOP with `requires_approval=True` — a human is notified and can decide manually. It never silently fails.

---

## 9. Node 4: approve

**File:** `agent/nodes/approve.py`

The graph is compiled with `interrupt_before=["approve"]`, so this node only runs *after* a human has submitted a decision. It does not run on the first pass through the graph — the graph pauses before entering it.

**What happens on first pass (before `approve` runs):**

Before the interrupt, the plan node has already stored the `ApprovalRequest` in Redis:
```
sre:approval:{approval_id}  →  ApprovalRequest(approved=None, ...)
```
The graph then pauses. The API's `/approvals/pending` endpoint reads this key and shows it to the human.

**What happens when the human approves (via UI or curl):**

`submit_approval()` in `api/routes/approvals.py`:
1. Loads the `ApprovalRequest` from Redis
2. Sets `approved=True/False`, `reviewer`, `notes`
3. Writes it back to Redis
4. Calls `resume_incident(thread_id, ...)` which calls `graph.astream(None, config)` — resumes from the frozen checkpoint

**What the approve node does when it finally runs:**

```python
async def approve_node(state: AgentState) -> dict:
    approval_request = state.get("approval_request")

    # Read the human's decision directly from Redis
    raw = await redis.get(f"sre:approval:{approval_request.id}")
    updated = ApprovalRequest.model_validate_json(raw)

    return {
        "approval_request": updated,
        "status": IncidentStatus.EXECUTING if updated.approved else IncidentStatus.FAILED,
    }
```

The node reads the decision from Redis rather than relying on LangGraph state injection. This avoids state merge ambiguity and keeps the full original graph state intact.

---

## 10. Node 5: execute

**File:** `agent/nodes/execute.py`

**Input:** `proposed_action`
**Output:** `action_result` (ActionResult)

```python
async def execute_node(state: AgentState) -> dict:
    action = state["proposed_action"]
    lock_resource = f"{action.action_type}:{action.target_namespace}:{action.target_resource}"

    async with action_lock(lock_resource) as acquired:
        if not acquired:
            return {"action_result": ActionResult(success=False, error="Already locked")}

        tool = ToolRegistry.get(_ACTION_TO_TOOL[action.action_type])
        result = await tool.run(
            namespace=action.target_namespace,
            pod_name=action.target_resource,
        )
```

The action-to-tool mapping:
```python
_ACTION_TO_TOOL = {
    ActionType.RESTART_POD:         "k8s_restart_pod",
    ActionType.SCALE_DEPLOYMENT:    "k8s_scale_deployment",
    ActionType.ROLLBACK_DEPLOYMENT: "k8s_rollback_deployment",
}
```

Nodes never import tools directly — they go through `ToolRegistry.get(name)`. Swapping a tool implementation does not require touching the node.

**Distributed lock (`tools/redis/locks.py`):**
Uses Redis `SET key value NX EX ttl` — atomic "set if not exists with expiry". If two agent instances try to act on the same resource simultaneously, only the first acquires the lock. The lock auto-expires after 120 seconds even if the process crashes.

**Rollback implementation (`tools/kubernetes/deployments.py`):**
`RollbackDeploymentTool` uses `replace_namespaced_deployment` (full PUT) instead of a strategic merge patch. This guarantees that all fields in the pod template — including `command`, `args`, and `env` — are completely overwritten to match the previous ReplicaSet. Strategic merge patch would leave omitted fields in place (e.g. a `command` override added by a bad deployment would persist after rollback).

---

## 11. Node 6: observe

**File:** `agent/nodes/observe.py`

`observe` always runs — it is the terminal node for every path: success, failure, NOOP, and rejection.

```python
async def observe_node(state: AgentState) -> dict:
    await asyncio.sleep(10)   # brief stabilisation wait

    # Re-sample metrics to verify recovery
    restarts = await get_pod_restart_count(alert.namespace)
    error_rate = await get_http_error_rate(alert.namespace)

    # Push one-line summary to Redis for future LLM context
    summary = _build_summary(state, restarts, error_rate)
    await push_incident_summary(summary)

    # Mark the incident resolved (or failed) in the long-term store
    await store.mark_resolved(incident.id)

    return {"status": state["status"]}
```

Three jobs:
1. **Verify** — re-query Prometheus to confirm the action helped
2. **Remember** — push a summary to Redis so future incident diagnoses have context on what was tried before
3. **Persist** — update the incident status in the long-term store

---

## 12. Tools

**File:** `tools/base.py`

Every tool extends `BaseTool`:

```python
class BaseTool(ABC):
    name: str           # registry key — must be unique
    description: str    # for future LLM tool-calling integration

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult: ...
```

`ToolResult`:
```python
@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""

    @classmethod
    def ok(cls, data): return cls(success=True, data=data)

    @classmethod
    def fail(cls, error): return cls(success=False, error=error)
```

`ToolRegistry` is a class-level dict (singleton). `register_all_tools()` is called once at startup in `api/main.py` lifespan. After that, any node can call `ToolRegistry.get("k8s_restart_pod")` without knowing where the class lives.

**Prometheus tools (`tools/prometheus/`):**
- `client.py` — async HTTP client for PromQL with retry logic (`tenacity`)
- `metrics.py` — named helpers (`get_cpu_usage`, `get_pod_restart_count`, etc.)
- `alerts.py` — fetch firing alerts from Alertmanager API

**Kubernetes tools (`tools/kubernetes/`):**
- `client.py` — builds the k8s API client (kubeconfig or in-cluster, cached with `@lru_cache`). Supports `K8S_SERVER_OVERRIDE` to redirect traffic to `host.docker.internal` when running inside Docker.
- `pods.py` — `RestartPodTool` (delete pod), `get_pod_logs()`, `list_pods()`
- `deployments.py` — `ScaleDeploymentTool`, `RollbackDeploymentTool`
- `events.py` — `list_recent_events()` (Warning events for diagnosis context)

---

## 13. Memory

**File:** `memory/schemas.py`

All domain models are Pydantic `BaseModel`:

```
AlertSignal       — raw alert from Alertmanager (input)
DiagnosisResult   — LLM root cause analysis output
ProposedAction    — what the agent wants to do
ApprovalRequest   — pending human decision (stored in Redis)
ActionResult      — what actually happened when executed
Incident          — the full lifecycle record
```

`Incident` is the container for all of the above, with a `status` field that progresses through:
`OPEN → DIAGNOSING → PLANNED → AWAITING_APPROVAL → EXECUTING → RESOLVED / FAILED`

**Short-term memory (`memory/short_term.py`):**
`AgentState` lives in RAM during the graph run, checkpointed to Redis after each node. Gone after the run completes.

**Long-term memory (`memory/long_term.py`):**
`IncidentStore` — persistent Redis store for `Incident` objects.

```
sre:incident:{uuid}      →  JSON blob of the full Incident
sre:incident:index       →  Sorted Set: {incident_id → timestamp}
```

The sorted set is how `list_recent()` works — `ZREVRANGE` returns IDs sorted by timestamp, then each is fetched individually.

**Approval requests** are stored separately under `sre:approval:{uuid}` with a TTL matching `approval_timeout_seconds`. They are read by both the approve node and the `/approvals/pending` endpoint.

---

## 14. Checkpointing

**File:** `memory/checkpointer.py`

```python
@asynccontextmanager
async def build_checkpointer():
    async with AsyncRedisSaver.from_conn_string(str(settings.redis_url)) as checkpointer:
        await checkpointer.asetup()   # creates RediSearch indexes
        yield checkpointer
```

How it works:

1. After each node completes, LangGraph serialises the full `AgentState` to Redis under a key derived from `thread_id`
2. `interrupt_before=["approve"]` saves state AND stops execution intentionally — the graph is frozen
3. When `resume_incident()` is called, it passes `config={"configurable": {"thread_id": ...}}` — LangGraph reads the saved state from Redis and continues from the last node

**Why `redis-stack-server` instead of plain `redis:alpine`:**
LangGraph's `AsyncRedisSaver` creates RediSearch indexes on startup (`FT.CREATE`) and queries them to retrieve checkpoints. Plain Redis does not have the Search module — it will fail with `unknown command 'FT._LIST'`.

---

## 15. The API layer

**File:** `api/main.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    configure_tracing()
    register_all_tools()    # once at startup
    yield

def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(LoggingMiddleware)
    app.include_router(alerts.router,    prefix="/alerts")
    app.include_router(approvals.router, prefix="/approvals")
    app.include_router(incidents.router, prefix="/incidents")
    app.include_router(health.router)
    app.include_router(ui.router,        prefix="/ui")
    app.mount("/metrics", make_asgi_app(registry=REGISTRY))
    return app
```

**`/alerts/` (`api/routes/alerts.py`):**

```python
@router.post("/", status_code=202)
async def receive_alertmanager_webhook(payload, background_tasks):
    firing = [a for a in payload.alerts if a.status == "firing"]
    for alert in firing:
        background_tasks.add_task(run_incident, _alertmanager_to_signal(alert))
    return {"dispatched": len(firing)}
```

Returns **202 Accepted** immediately. The agent runs in the background via `BackgroundTasks`. Alertmanager has a short response timeout — if the webhook takes too long it retries, which would create duplicate incidents. Returning 202 immediately prevents this.

**`/approvals/{id}` (`api/routes/approvals.py`):**

```python
@router.post("/{approval_id}")
async def submit_approval(approval_id, decision):
    approval = ApprovalRequest.model_validate_json(await redis.get(key))
    approval.approved = decision.approved
    approval.reviewer = decision.reviewer
    await redis.set(key, approval.model_dump_json(), ex=3600)

    incident_record = await store.get(approval.incident_id)
    incident = await resume_incident(
        thread_id=incident_record.thread_id,   # LangGraph thread, not incident ID
        approval_data={"approval_request": approval},
    )
    return {"approved": decision.approved, "incident_status": incident.status}
```

Note: `incident_record.thread_id` is used to resume the graph, not `approval.incident_id`. The incident store lookup is required to get the LangGraph thread ID.

**`/ui` (`api/routes/ui.py`):**

Single-page HTML dashboard served directly from FastAPI. No build step, no external dependencies. Shows:
- **Pending Approvals tab** — approval cards with Approve/Reject buttons, risk level, rationale, expiry countdown
- **Incidents tab** — table of recent incidents with status, action taken, result
- Auto-refreshes every 5 seconds

**Auth (`api/middleware/auth.py`):**
Every route except `/healthz` and `/readyz` requires `X-API-Key` header matching `settings.api_key`. Alertmanager sends `Authorization: ApiKey <key>` — the middleware accepts both formats.

---

## 16. Configuration

**File:** `configs/settings.py`

Loading order — last wins:

```
configs/base.yaml
      +
configs/{ENV}.yaml          ENV env var selects the overlay
      +
environment variables       set by docker-compose environment: or k8s ConfigMap/Secret
      +
.env file                   local dev only — excluded from Docker image via .dockerignore
```

Environment variables (from `docker-compose.yaml`'s `environment:` block) always override YAML. This is how the container gets `REDIS_URL=redis://redis:6379/0` instead of `localhost:6379` from `base.yaml`, without touching any config files.

`get_settings()` is decorated with `@lru_cache(maxsize=1)` — settings are loaded once per process and cached. Every call returns the same object.

---

## 17. Observability

**Logging (`observability/logging.py`):**
`structlog` for structured JSON. Every log line is a JSON object:
```json
{"event": "agent run started", "alert": "PodCrashLooping", "incident_id": "...", "level": "info"}
```
In a terminal (TTY) it pretty-prints with colours. In Docker/k8s it outputs JSON for Loki/CloudWatch/Datadog ingestion.

**Metrics (`observability/metrics.py`):**
The agent exposes its own Prometheus metrics at `/metrics`:
- `sre_agent_incidents_total` — counter by status and alert name
- `sre_agent_node_duration_seconds` — histogram per node
- `sre_agent_actions_executed_total` — counter by action type and success/fail
- `sre_agent_approvals_pending` — gauge

This is separate from the in-cluster Prometheus that monitors Kubernetes. The Docker Compose Prometheus on port 9090 scrapes only this agent endpoint.

**Tracing (`observability/tracing.py`):**
OpenTelemetry configured to export to an OTLP endpoint. Not yet instrumented at the per-node level.

---

## 18. End-to-end data flow

### Auto-remediation path (PodCrashLooping → restart_pod, no approval)

```
1. Alertmanager fires PodCrashLooping
   └── POSTs to /alerts/ with Alertmanager webhook payload

2. FastAPI receive_alertmanager_webhook() [api/routes/alerts.py]
   └── returns 202 immediately
   └── schedules run_incident(alert) as background task

3. run_incident() [agent/core/agent.py]
   └── acquires dedup lock: sre:incident_lock:PodCrashLooping:default
   └── creates Incident(status=OPEN), saves to Redis
   └── builds initial AgentState (all fields None)
   └── opens Redis checkpointer, compiles + streams the graph

4. detect node
   └── parallel queries to Prometheus + k8s API
   └── finds crash-app with 5 restarts → fetches last 50 log lines
   └── returns {raw_metrics, raw_logs, raw_k8s_events}
   └── LangGraph checkpoints to Redis

5. route_after_detect() → "diagnose"

6. diagnose node
   └── formats all raw data into structured context text
   └── sends to GPT-4o: system=diagnose.md, user=context
   └── LLM returns JSON: {root_cause, confidence: 0.92, ...}
   └── returns {diagnosis}
   └── LangGraph checkpoints to Redis

7. route_after_diagnose() → confidence 0.92 ≥ 0.5 → "plan"

8. plan node
   └── sends diagnosis to GPT-4o: system=plan.md
   └── LLM returns JSON: {action_type: restart_pod, target_resource: crash-app, ...}
   └── _needs_approval(): restart_pod + dev env → requires_approval=False
   └── returns {proposed_action}
   └── LangGraph checkpoints to Redis

9. route_after_plan() → requires_approval=False → "execute" (skips approve)

10. execute node
    └── acquires lock: restart_pod:default:crash-app
    └── ToolRegistry.get("k8s_restart_pod") → RestartPodTool
    └── CoreV1Api.delete_namespaced_pod("crash-app", "default")
    └── returns {action_result(success=True)}
    └── LangGraph checkpoints to Redis

11. route_after_execute() → "observe"

12. observe node
    └── waits 10s, re-queries Prometheus
    └── pushes summary to Redis memory
    └── marks incident RESOLVED in store
    └── LangGraph checkpoints final state

13. run_incident() resumes
    └── reads final state via graph.aget_state()
    └── syncs diagnosis, action, result onto Incident record
    └── saves Incident(status=RESOLVED) to Redis
    └── releases dedup lock
```

### HITL path (DeploymentReplicasMismatch → rollback_deployment, requires approval)

Steps 1–8 are the same. The difference starts at plan:

```
8. plan node
   └── LLM returns: {action_type: rollback_deployment, ...}
   └── _needs_approval(): rollback → always True
   └── returns {proposed_action, requires_approval=True}

9. route_after_plan() → requires_approval=True → "approve"

   ┌── interrupt_before=["approve"] triggers ──────────────────────────┐
   │  LangGraph saves full AgentState to Redis (checkpoint)            │
   │  graph.astream() returns — graph is frozen                        │
   │  run_incident() completes with status=AWAITING_APPROVAL           │
   └───────────────────────────────────────────────────────────────────┘

   Meanwhile, approve node stores ApprovalRequest in Redis:
   sre:approval:{uuid} → {approved: None, action: rollback_deployment, ...}

10. Human opens http://localhost:8000/ui
    └── GET /approvals/pending → reads sre:approval:* keys from Redis
    └── Dashboard shows approval card with Approve/Reject buttons

11. Human clicks Approve

12. POST /approvals/{id} [api/routes/approvals.py]
    └── loads ApprovalRequest from Redis
    └── sets approved=True, reviewer="dashboard"
    └── writes updated record back to Redis
    └── looks up incident to get thread_id
    └── calls resume_incident(thread_id=...)

13. resume_incident() [agent/core/agent.py]
    └── graph.astream(None, config) — resumes from frozen checkpoint

14. approve node now runs (for the first and only time)
    └── reads ApprovalRequest from Redis: approved=True
    └── returns {approval_request: updated, status: EXECUTING}

15. route_after_approve() → approved=True → "execute"

16. execute node
    └── RollbackDeploymentTool.run(deployment_name="web-app", ...)
    └── finds previous ReplicaSet (nginx:alpine spec)
    └── replace_namespaced_deployment (full PUT) — overwrites all fields
    └── nginx:alpine pods start, deployment becomes healthy

17. observe + run_incident() → Incident(status=RESOLVED) saved
    └── dedup lock released
```

---

## Key design decisions

| Decision | Why |
|---|---|
| LangGraph StateGraph | Explicit, debuggable node transitions instead of a monolithic agent loop |
| Nodes return deltas, not full state | Each node only owns what it changes — no accidental overwrites |
| `add_messages` reducer | LLM history accumulates across nodes without any node knowing about others |
| Tools behind a registry | Nodes are decoupled from implementations — swap without touching node code |
| `interrupt_before` for HITL | Graph state is frozen in Redis — resume after minutes or hours without losing context |
| Approve node reads from Redis | Avoids LangGraph state merge ambiguity; full original state is preserved on resume |
| `replace_namespaced_deployment` for rollback | Full PUT overwrites all fields (including `command`/`args`); strategic patch leaves omitted fields in place |
| Distributed locks for execution | Prevents duplicate remediation when Alertmanager re-sends or multiple instances run |
| `return_exceptions=True` in gather | One failing tool doesn't abort the detection phase |
| Dedup lock per (alert_name, namespace) | Alertmanager repeat_interval resends — one active incident at a time per problem |
| YAML as lowest-priority config | Env vars always win — containers get the right config without touching YAML |
| `redis-stack-server` not plain redis | LangGraph checkpointer requires RediSearch for checkpoint indexing |

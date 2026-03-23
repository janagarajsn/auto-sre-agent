# Codebase Walkthrough — auto-sre-agent

A deep dive into every layer of the codebase. Read this top to bottom — each section builds on the previous one.

---

## Table of Contents

1. [The Big Picture](#1-the-big-picture)
2. [Entry Point — how an incident starts](#2-entry-point)
3. [The State — the shared whiteboard](#3-the-state)
4. [The Graph — how nodes are wired together](#4-the-graph)
5. [The Router — conditional edges](#5-the-router)
6. [Node 1: detect](#6-node-1-detect)
7. [Node 2: diagnose](#7-node-2-diagnose)
8. [Node 3: plan](#8-node-3-plan)
9. [Node 4: approve](#9-node-4-approve)
10. [Node 5: execute](#10-node-5-execute)
11. [Node 6: observe](#11-node-6-observe)
12. [Tools — how the agent talks to the outside world](#12-tools)
13. [Memory — short-term vs long-term](#13-memory)
14. [Checkpointing — how graph resumption works](#14-checkpointing)
15. [The API layer](#15-the-api-layer)
16. [Configuration — settings loading order](#16-configuration)
17. [Observability — logging, metrics, tracing](#17-observability)
18. [The full data flow end to end](#18-the-full-data-flow-end-to-end)

---

## 1. The Big Picture

The agent is a **LangGraph state machine** wrapped in a **FastAPI service**, backed by **Redis** for memory, talking to **Prometheus** for metrics and **Kubernetes** for actions.

```
Alertmanager (or simulate_incident.py)
        │
        ▼
  FastAPI /alerts/        ← api/routes/alerts.py
        │
        ▼
  run_incident()          ← agent/core/agent.py
        │
        ▼
  LangGraph pipeline
  ┌────────────────────────────────────────────────────────────┐
  │  detect → diagnose → plan → [approve] → execute → observe  │
  └────────────────────────────────────────────────────────────┘
        │              │              │
   Prometheus      OpenAI LLM    Kubernetes API
        │
        ▼
  Redis (incident store + checkpointer)
```

Two things flow through this system:
- **AgentState** — the in-memory whiteboard shared across all nodes (lost when process dies)
- **Incident** — the persisted record saved to Redis (survives restarts)

---

## 2. Entry Point

**File:** `agent/core/agent.py` → `run_incident()`

This is the first function called when an alert arrives, whether from the API webhook or `simulate_incident.py`.

```python
async def run_incident(alert: AlertSignal) -> Incident:
    incident_id = uuid.uuid4()   # permanent ID for this incident
    thread_id = str(uuid.uuid4()) # LangGraph's handle for checkpointing

    incident = Incident(id=incident_id, alert=alert, thread_id=thread_id)

    store = await get_incident_store()
    await store.save(incident)   # saved immediately — even if graph crashes, record exists
    ...
```

**Why two UUIDs?**
- `incident_id` — identifies the business event (the crash, the alert)
- `thread_id` — identifies the graph execution. These are separate because in the future one incident could spawn multiple graph runs (e.g. retry after failure)

**The initial_state:**
```python
initial_state: AgentState = {
    "incident_id": incident_id,
    "alert": alert,
    "diagnosis": None,         # will be populated by diagnose node
    "proposed_action": None,   # will be populated by plan node
    "action_result": None,     # will be populated by execute node
    "status": IncidentStatus.OPEN,
    "messages": [],            # LLM conversation history
    "raw_metrics": {},         # intermediate data, not persisted long-term
    ...
}
```

Everything starts as `None`. Each node reads what it needs and writes back what it produces.

**The graph run:**
```python
async with build_checkpointer() as checkpointer:
    graph = build_sre_graph(checkpointer=checkpointer)

    async for event in graph.astream(initial_state, config=config):
        node_name = next(iter(event), "unknown")
        # each iteration = one node completed
```

`astream()` runs the graph node by node. Each `event` is a dict like `{"detect": {...state delta...}}`. The `config` carries the `thread_id` so the checkpointer knows which Redis key to write to.

After streaming completes, `aget_state()` reads the final state from Redis and syncs it back to the `Incident` object, which is then saved.

---

## 3. The State

**File:** `memory/short_term.py`

`AgentState` is a `TypedDict` — a Python dict with typed keys. LangGraph passes this between nodes. Each node receives the full state and returns only the fields it changed (a delta). LangGraph merges the delta back into the canonical state.

```python
class AgentState(TypedDict):
    incident_id: UUID           # never changes
    thread_id: str              # never changes
    alert: AlertSignal          # the input — never changes

    diagnosis: DiagnosisResult | None       # set by diagnose node
    proposed_action: ProposedAction | None  # set by plan node
    approval_request: ApprovalRequest | None # set by approve node
    action_result: ActionResult | None      # set by execute node

    status: IncidentStatus      # updated by each node
    requires_approval: bool     # set by plan node
    error: str | None           # set if something goes wrong

    messages: Annotated[list[Any], add_messages]  # special — append-only
    raw_metrics: dict[str, Any]   # fetched by detect, consumed by diagnose
    raw_logs: list[str]           # fetched by detect, consumed by diagnose
    raw_k8s_events: list[...]     # fetched by detect, consumed by diagnose

    incident: Incident            # the full persisted record
```

**The `messages` field is special.**
`Annotated[list[Any], add_messages]` tells LangGraph to use the `add_messages` reducer for this field. Instead of replacing the list on each node update, it *appends* to it. This is how LangGraph accumulates the LLM conversation history across nodes — each node adds its messages, none overwrite the previous ones.

**Why keep `raw_metrics`, `raw_logs`, `raw_k8s_events` separate?**
These are intermediate scratchpad data. The `detect` node fetches them, the `diagnose` node consumes them to build the LLM prompt, and they're never saved to the long-term `Incident` record. Keeping them separate from `diagnosis` makes the data flow explicit.

---

## 4. The Graph

**File:** `agent/workflows/sre_graph.py`

```python
def build_sre_graph(checkpointer=None):
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("detect", detect_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("plan", plan_node)
    graph.add_node("approve", approve_node)
    graph.add_node("execute", execute_node)
    graph.add_node("observe", observe_node)

    # Entry
    graph.add_edge(START, "detect")

    # Conditional edges
    graph.add_conditional_edges("detect", route_after_detect, {...})
    graph.add_conditional_edges("diagnose", route_after_diagnose, {...})
    graph.add_conditional_edges("plan", route_after_plan, {...})
    graph.add_conditional_edges("approve", route_after_approve, {...})
    graph.add_conditional_edges("execute", route_after_execute, {...})

    # Terminal
    graph.add_edge("observe", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["approve"],  # pause HERE for human approval
    )
```

**`add_node(name, function)`** — registers an async function as a node. The function signature must be `async def node(state: AgentState) -> dict`.

**`add_conditional_edges(source, condition_fn, mapping)`** — after `source` node completes, calls `condition_fn(state)` which returns a string. That string is looked up in `mapping` to find the next node.

**`interrupt_before=["approve"]`** — this is the human-in-the-loop gate. When the graph reaches the `approve` node, it pauses *before* entering it and waits. The graph state is saved to Redis. The API's `/approvals/{id}` endpoint resumes it later.

**Why compile?**
`graph.compile()` validates the graph (checks for unreachable nodes, missing edges), attaches the checkpointer, and returns an executable object. You can't run the graph without compiling it first.

---

## 5. The Router

**File:** `agent/core/router.py`

Each function here is a conditional edge — it inspects the current state and returns the name of the next node.

```python
def route_after_diagnose(state: AgentState) -> str:
    diagnosis = state.get("diagnosis")
    if diagnosis is None or diagnosis.confidence < 0.5:
        return "observe"   # not enough confidence — skip to end
    return "plan"          # confident enough — proceed to planning
```

```python
def route_after_plan(state: AgentState) -> str:
    action = state.get("proposed_action")
    if action is None:
        return "observe"
    if action.requires_approval:
        return "approve"   # high risk — human must decide
    return "execute"       # low risk — execute directly
```

```python
def route_after_approve(state: AgentState) -> str:
    approval = state.get("approval_request")
    if approval.approved is True:
        return "execute"
    return "observe"       # rejected or timed out — skip execution
```

**The safety principle:** every router has a default path back to `observe`. If anything is missing, unclear, or rejected, the graph exits gracefully through `observe` rather than crashing or getting stuck.

---

## 6. Node 1: detect

**File:** `agent/nodes/detect.py`

**Input:** `alert` (AlertSignal)
**Output:** `raw_metrics`, `raw_logs`, `raw_k8s_events`

```python
async def detect_node(state: AgentState) -> dict:
    alert = state["alert"]
    namespace = alert.namespace

    # All queries run in parallel via asyncio.gather
    (cpu, mem, restarts, error_rate, events, pods) = await asyncio.gather(
        get_cpu_usage(namespace),
        get_memory_usage(namespace),
        get_pod_restart_count(namespace),
        get_http_error_rate(namespace),
        list_recent_events(namespace, event_type="Warning"),
        list_pods(namespace),
        return_exceptions=True,   # don't crash if one tool fails
    )

    # Collect logs for high-restart pods
    for pod in pods:
        if pod.get("restarts", 0) >= 3:
            log = await get_pod_logs(namespace, pod["name"], tail_lines=50)
            logs.append(log)

    return {
        "raw_metrics": {"cpu": cpu, "memory": mem, "restarts": restarts, ...},
        "raw_logs": logs,
        "raw_k8s_events": events,
        "status": IncidentStatus.DIAGNOSING,
    }
```

**`return_exceptions=True`** in `asyncio.gather` is important. If Prometheus is down or a k8s call fails, the exception is returned as a value (not raised). The node then checks `isinstance(result, Exception)` and substitutes an empty list. One tool failing doesn't kill the entire detection phase.

**Why gather all metrics simultaneously?**
Each Prometheus query is an HTTP call (~10-100ms). Running 5 queries sequentially wastes 400-500ms. `asyncio.gather` runs them all concurrently and waits for all to finish — total time = slowest single query, not the sum.

---

## 7. Node 2: diagnose

**File:** `agent/nodes/diagnose.py`

**Input:** `raw_metrics`, `raw_logs`, `raw_k8s_events`, `alert`
**Output:** `diagnosis` (DiagnosisResult)

```python
async def diagnose_node(state: AgentState) -> dict:
    llm = ChatOpenAI(model=settings.openai_model, temperature=0.0)

    system_prompt = load_prompt("diagnose")  # reads agent/prompts/diagnose.md
    user_content = _build_context(state)     # formats all raw data into text

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ])

    diagnosis = _parse_diagnosis(response.content)  # extracts JSON from response
    return {"diagnosis": diagnosis, "messages": [...]}
```

**`temperature=0.0`** — zero temperature means the LLM is deterministic and focused. For diagnosis and planning you want the most likely answer, not creative variation.

**`_build_context(state)`** — assembles the raw data into a structured text block:
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

The LLM sees this structured context and responds with a JSON block (enforced by `agent/prompts/diagnose.md`).

**`_parse_diagnosis()`** — looks for a ` ```json ... ``` ` block in the LLM response using regex. If the LLM returns well-formed JSON, it deserialises it into a `DiagnosisResult`. If not (LLM hallucinated or wrapped it differently), it falls back to wrapping the raw text with `confidence=0.5`.

**Why `load_prompt("diagnose")`?**
Prompts live in `agent/prompts/diagnose.md` as Markdown files, not hardcoded strings. This means you can edit prompt instructions without touching Python code. The `load_prompt()` function reads the file and caches it with `@lru_cache`.

---

## 8. Node 3: plan

**File:** `agent/nodes/plan.py`

**Input:** `diagnosis`, `alert`
**Output:** `proposed_action` (ProposedAction)

```python
async def plan_node(state: AgentState) -> dict:
    llm = ChatOpenAI(model=settings.openai_model, temperature=0.0)

    response = await llm.ainvoke([
        SystemMessage(content=load_prompt("plan")),
        HumanMessage(content=f"## Diagnosis\n{diagnosis.root_cause}\n\n## Alert\n..."),
    ])

    proposed = _parse_action(response.content, alert.namespace)
    proposed.requires_approval = _needs_approval(proposed, settings.is_production)
    ...
```

**`_needs_approval()`** — applies business rules on top of what the LLM said:
```python
def _needs_approval(action, is_prod):
    if action.action_type in {ActionType.ROLLBACK_DEPLOYMENT, ActionType.CORDON_NODE}:
        return True   # always requires approval — too destructive
    if action.action_type in {ActionType.RESTART_POD} and not is_prod:
        return False  # auto-approve in dev
    return action.risk_level in (Severity.HIGH, Severity.CRITICAL)
```

The LLM proposes `requires_approval: true/false` in its JSON, but this function **overrides that** with hard rules. The LLM doesn't get to decide whether a rollback needs approval — the code does. This is a deliberate safety boundary.

**Fallback to NOOP:**
If the LLM response can't be parsed into a `ProposedAction`, the node returns:
```python
ProposedAction(action_type=ActionType.NOOP, requires_approval=True, ...)
```
A NOOP with approval required means a human will be notified and can decide what to do manually. Never silently fail.

---

## 9. Node 4: approve

**File:** `agent/nodes/approve.py`

This node has two execution paths depending on whether it's being run for the first time or being resumed after a human decision.

**First pass (graph hits approve for the first time):**
```python
# Create and persist the approval request
approval_request = ApprovalRequest(
    id=uuid4(),
    incident_id=incident_id,
    proposed_action=action,
    expires_at=datetime.utcnow() + timedelta(seconds=timeout),
)
await redis.set(key, approval_request.model_dump_json(), ex=timeout)

# SUSPEND the graph here
interrupt({
    "type": "approval_required",
    "approval_id": str(approval_request.id),
    "action": action.model_dump(),
    "expires_at": ...,
})
```

`interrupt()` is a LangGraph primitive. It raises a special exception that LangGraph catches, saves the full graph state to Redis (via the checkpointer), and stops execution. The API returns to the caller. The graph is now frozen in time.

**Resumed pass (human POSTed to /approvals/{id}):**
```python
# approval_request is already in state with approved=True/False
if approval.approved is True:
    return {"status": IncidentStatus.EXECUTING}
else:
    return {"status": IncidentStatus.FAILED}
```

When `resume_incident()` is called from the API, it calls `graph.aupdate_state()` to inject the human's decision into the frozen state, then calls `graph.astream(None, ...)` to resume from where it stopped.

---

## 10. Node 5: execute

**File:** `agent/nodes/execute.py`

**Input:** `proposed_action`
**Output:** `action_result` (ActionResult)

```python
async def execute_node(state: AgentState) -> dict:
    action = state["proposed_action"]

    # Distributed lock — prevents two agent instances acting on the same resource
    lock_resource = f"{action.action_type}:{action.target_namespace}:{action.target_resource}"

    async with action_lock(lock_resource) as acquired:
        if not acquired:
            return {"action_result": ActionResult(success=False, error="Already locked"), ...}

        tool = ToolRegistry.get(_ACTION_TO_TOOL[action.action_type])
        result = await tool.run(
            namespace=action.target_namespace,
            pod_name=action.target_resource,  # or deployment_name depending on action type
            **action.parameters,
        )
```

**The action-to-tool mapping:**
```python
_ACTION_TO_TOOL = {
    ActionType.RESTART_POD: "k8s_restart_pod",
    ActionType.SCALE_DEPLOYMENT: "k8s_scale_deployment",
    ActionType.ROLLBACK_DEPLOYMENT: "k8s_rollback_deployment",
}
```

Nodes never import tools directly. They go through `ToolRegistry.get("tool_name")`. This decoupling means you can swap a tool implementation without touching the node code.

**The distributed lock (`tools/redis/locks.py`):**
```python
async with action_lock("restart_pod:default:crash-app") as acquired:
    ...
```

Uses Redis `SET key value NX EX ttl` — atomic "set if not exists with expiry". If two agent instances both try to restart the same pod at the same time, only the first one acquires the lock. The second sees `acquired=False` and skips. The lock auto-expires after 120 seconds even if the process crashes.

---

## 11. Node 6: observe

**File:** `agent/nodes/observe.py`

**Input:** full state after execution
**Output:** final status update

```python
async def observe_node(state: AgentState) -> dict:
    await asyncio.sleep(10)  # brief stabilisation wait

    # Re-sample metrics to verify recovery
    restarts = await get_pod_restart_count(alert.namespace)
    error_rate = await get_http_error_rate(alert.namespace)

    # Push summary to Redis short-term memory for future LLM context
    summary = _build_summary(state, restarts, error_rate)
    await push_incident_summary(summary)

    # Update long-term incident store
    store = await get_incident_store()
    await store.mark_resolved(incident.id)

    return {"status": state["status"]}
```

`observe` always runs — it's the terminal node for every path (success, failure, NOOP, rejection). It has three jobs:
1. **Verify** — re-query Prometheus to see if the action helped
2. **Remember** — push a one-line summary to Redis so future incidents can reference what happened
3. **Persist** — mark the incident resolved (or failed) in the long-term store

---

## 12. Tools

**File:** `tools/base.py`

Every tool extends `BaseTool`:
```python
class BaseTool(ABC):
    name: str          # registry key — must be unique
    description: str   # for future LLM tool-calling integration

    @abstractmethod
    async def run(self, **kwargs) -> ToolResult: ...
```

`ToolResult` is a simple dataclass:
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

**`ToolRegistry`** is a class-level dict (singleton by nature):
```python
class ToolRegistry:
    _tools: dict[str, BaseTool] = {}

    @classmethod
    def register(cls, tool): cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name): return cls._tools[name]
```

`register_all_tools()` is called once at startup (in `api/main.py` lifespan and in `simulate_incident.py`). It instantiates every tool and registers it. After that, any node can call `ToolRegistry.get("k8s_restart_pod")` without knowing where the class lives.

**Prometheus tools (`tools/prometheus/`):**
- `client.py` — raw async HTTP client for PromQL queries with retry logic (`tenacity`)
- `metrics.py` — named helpers like `get_cpu_usage(namespace)` that build PromQL strings
- `alerts.py` — fetches firing alerts from Alertmanager API

**Kubernetes tools (`tools/kubernetes/`):**
- `client.py` — builds the k8s API client (in-cluster vs kubeconfig, cached with `@lru_cache`)
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

`Incident` is the container for all of the above:
```python
class Incident(BaseModel):
    id: UUID
    status: IncidentStatus    # OPEN → DIAGNOSING → PLANNED → EXECUTING → RESOLVED
    alert: AlertSignal
    diagnosis: DiagnosisResult | None
    proposed_action: ProposedAction | None
    approval: ApprovalRequest | None
    action_result: ActionResult | None
    created_at: datetime
    updated_at: datetime
    thread_id: str            # links back to LangGraph checkpoint
```

**Short-term memory (`memory/short_term.py`):**
`AgentState` — lives only for the duration of the graph run. Stored in RAM + Redis checkpoints. Gone when the graph finishes.

**Long-term memory (`memory/long_term.py`):**
`IncidentStore` — Redis-backed store for `Incident` objects.

```python
# Storage structure in Redis:
"sre:incident:{uuid}"        → JSON blob of the Incident
"sre:incident:index"         → Sorted Set: {incident_id → timestamp}
```

The sorted set is how `list_recent()` works — `ZREVRANGE` returns IDs sorted by timestamp, then each ID is fetched individually. This pattern is standard for Redis "index + record" storage.

---

## 14. Checkpointing

**File:** `memory/checkpointer.py`

```python
@asynccontextmanager
async def build_checkpointer():
    async with AsyncRedisSaver.from_conn_string(str(settings.redis_url)) as checkpointer:
        await checkpointer.asetup()  # creates RediSearch indexes for checkpoint queries
        yield checkpointer
```

LangGraph checkpointing works like this:

1. After each node completes, LangGraph serialises the full `AgentState` to Redis under a key derived from `thread_id`
2. If the process crashes, the state survives in Redis
3. When `resume_incident()` is called, it passes `config={"configurable": {"thread_id": ...}}` — LangGraph reads the saved state from that key and continues from the last checkpoint
4. `interrupt()` in the approve node is a special checkpoint — it saves state AND stops execution intentionally

**Why `AsyncRedisSaver` needs RediSearch (`FT._LIST` command):**
LangGraph stores checkpoints in Redis as JSON and creates RediSearch indexes so it can query them efficiently (e.g. "give me the latest checkpoint for thread X"). This is why plain `redis:7-alpine` fails — it doesn't have the Search module. `redis/redis-stack-server` bundles it.

---

## 15. The API Layer

**File:** `api/main.py`

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    configure_tracing()
    register_all_tools()    # tools registered once here at startup
    yield
    # shutdown cleanup here

def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(LoggingMiddleware)
    app.include_router(alerts.router, prefix="/alerts")
    app.include_router(approvals.router, prefix="/approvals")
    app.include_router(incidents.router, prefix="/incidents")
    app.include_router(health.router)
    app.mount("/metrics", make_asgi_app(registry=REGISTRY))  # Prometheus scrape endpoint
    return app
```

**`/alerts/` route (`api/routes/alerts.py`):**
```python
@router.post("/", status_code=202)
async def receive_alertmanager_webhook(payload, background_tasks):
    firing = [a for a in payload.alerts if a.status == "firing"]
    for alert in firing:
        signal = _alertmanager_to_signal(alert)
        background_tasks.add_task(run_incident, signal)  # non-blocking
    return {"dispatched": len(firing)}
```

Returns **202 Accepted** immediately. The agent runs in the background via `BackgroundTasks`. Alertmanager has a short timeout — if the webhook takes too long it retries. Returning 202 immediately prevents duplicate triggers.

**`/approvals/{id}` route (`api/routes/approvals.py`):**
```python
@router.post("/{approval_id}")
async def submit_approval(approval_id, decision):
    # 1. Load the ApprovalRequest from Redis
    approval = ApprovalRequest.model_validate_json(await redis.get(key))
    # 2. Update with the human's decision
    approval.approved = decision.approved
    approval.reviewer = decision.reviewer
    # 3. Resume the frozen graph
    incident = await resume_incident(
        thread_id=str(approval.incident_id),
        approval_data={"approval_request": approval},
    )
    return {"approved": decision.approved, "incident_status": incident.status}
```

**Auth (`api/middleware/auth.py`):**
Every route (except `/healthz` and `/readyz`) requires `X-API-Key` header. The value must match `settings.api_key`. Simple but sufficient for internal use — for production you'd use JWT or OAuth2.

---

## 16. Configuration

**File:** `configs/settings.py`

```python
def get_settings() -> Settings:
    defaults = _merged_yaml_defaults()
    # Only use YAML as fallback — env vars always win
    env_keys = {k.lower() for k in os.environ}
    filtered = {k: v for k, v in defaults.items() if k.lower() not in env_keys}
    return Settings(**filtered)
```

Loading order (lowest to highest precedence):

```
configs/base.yaml
      +
configs/{ENV}.yaml      (ENV env var selects which overlay)
      +
environment variables   (set by docker-compose or Kubernetes)
      +
.env file               (local dev only, never copied into Docker image)
```

**Why this order matters:**
- `base.yaml` sets safe defaults (localhost URLs for local dev)
- `{ENV}.yaml` overrides for that environment (e.g. `prod.yaml` sets `k8s_in_cluster: true`)
- Environment variables (from docker-compose `environment:` block or k8s ConfigMap/Secret) always win — this is how the container gets `REDIS_URL=redis://redis:6379/0` instead of `localhost`
- `.env` is excluded from the Docker image via `.dockerignore` — it only affects local runs

**`@lru_cache(maxsize=1)`** on `get_settings()` means settings are loaded exactly once per process and cached. Every call to `get_settings()` returns the same object. This is safe because settings don't change at runtime.

---

## 17. Observability

**Logging (`observability/logging.py`):**
Uses `structlog` for structured JSON logging. Every log line is a JSON object:
```json
{"event": "agent run started", "alert": "PodCrashLooping", "incident_id": "...", "level": "info", "timestamp": "..."}
```
In a terminal (TTY) it pretty-prints with colours. In Docker/k8s it outputs JSON for ingestion by Loki/CloudWatch/Datadog.

**Metrics (`observability/metrics.py`):**
The agent exposes its own Prometheus metrics at `/metrics`:
- `sre_agent_incidents_total` — counter by status and alert name
- `sre_agent_node_duration_seconds` — histogram per node (detect, diagnose, plan, etc.)
- `sre_agent_actions_executed_total` — counter by action type and success/fail
- `sre_agent_approvals_pending` — gauge

This is separate from the Prometheus that monitors your cluster. Docker Compose Prometheus scrapes the agent's `/metrics` endpoint.

**Tracing (`observability/tracing.py`):**
OpenTelemetry setup that exports traces to an OTLP endpoint (Jaeger, Tempo, etc.). Currently not wired up to individual nodes — that would be the next observability step.

---

## 18. The Full Data Flow End to End

Here is the complete journey of your `simulate_incident.py` run:

```
1. simulate_incident.py
   └── creates AlertSignal(alert_name="PodCrashLooping", namespace="default", pod="crash-app")
   └── calls run_incident(alert)

2. run_incident() [agent/core/agent.py]
   └── generates incident_id + thread_id
   └── saves Incident(status=OPEN) to Redis
   └── builds initial AgentState (everything None)
   └── opens Redis checkpointer
   └── compiles + streams the graph

3. detect node [agent/nodes/detect.py]
   └── reads alert.namespace from state
   └── fires 6 async queries in parallel:
       ├── Prometheus: CPU usage for namespace
       ├── Prometheus: memory usage
       ├── Prometheus: pod restart counts → finds crash-app with 5 restarts
       ├── Prometheus: HTTP error rate
       ├── k8s API: Warning events → finds BackOff events for crash-app
       └── k8s API: list pods → crash-app has restarts >= 3
   └── fetches last 50 lines of crash-app logs → "OOM error"
   └── returns {raw_metrics, raw_logs, raw_k8s_events, status=DIAGNOSING}
   └── LangGraph checkpoints state to Redis

4. route_after_detect()
   └── no error → returns "diagnose"

5. diagnose node [agent/nodes/diagnose.py]
   └── formats all raw data into structured text
   └── sends to GPT-4o with system prompt from agent/prompts/diagnose.md
   └── LLM responds with JSON:
       {summary, root_cause, confidence: 0.92, supporting_metrics, supporting_logs}
   └── parses JSON into DiagnosisResult
   └── returns {diagnosis, status=PLANNED, messages=[...]}
   └── LangGraph checkpoints state to Redis

6. route_after_diagnose()
   └── confidence 0.92 >= 0.5 → returns "plan"

7. plan node [agent/nodes/plan.py]
   └── sends diagnosis + alert to GPT-4o with agent/prompts/plan.md
   └── LLM responds with JSON:
       {action_type: "restart_pod", target_resource: "crash-app", risk_level: "low", ...}
   └── _needs_approval() → restart_pod + dev env → requires_approval=False
   └── returns {proposed_action, status=EXECUTING}
   └── LangGraph checkpoints state to Redis

8. route_after_plan()
   └── requires_approval=False → returns "execute" (skips approve node)

9. execute node [agent/nodes/execute.py]
   └── acquires Redis lock: "restart_pod:default:crash-app"
   └── ToolRegistry.get("k8s_restart_pod") → RestartPodTool
   └── RestartPodTool.run(namespace="default", pod_name="crash-app")
       └── calls k8s CoreV1Api.delete_namespaced_pod("crash-app", "default")
       └── pod is deleted → Deployment controller recreates it (if managed by Deployment)
   └── releases Redis lock
   └── returns {action_result(success=True), status=RESOLVED}
   └── LangGraph checkpoints state to Redis

10. route_after_execute()
    └── always returns "observe"

11. observe node [agent/nodes/observe.py]
    └── waits 10 seconds
    └── re-queries Prometheus for restart counts (verification)
    └── builds summary string and pushes to Redis memory
    └── calls store.mark_resolved(incident.id) → updates Redis record
    └── returns {status=RESOLVED}
    └── LangGraph checkpoints final state to Redis

12. run_incident() resumes after astream()
    └── calls graph.aget_state() → reads final state from Redis
    └── syncs diagnosis, proposed_action, action_result, status onto Incident object
    └── saves final Incident(status=RESOLVED) to Redis
    └── returns Incident to simulate_incident.py

13. simulate_incident.py prints:
    Incident completed:
      ID:     654f9655-...
      Status: resolved
      Root cause: The pod crash-app is repeatedly crashing...
      Action: restart_pod on crash-app
      Result: success
```

---

## Key Design Decisions Summary

| Decision | Why |
|---|---|
| LangGraph StateGraph | Explicit, debuggable node transitions instead of a monolithic agent loop |
| Nodes return deltas, not full state | Each node only owns what it changes — no accidental overwrites |
| `add_messages` reducer | LLM history accumulates across nodes without nodes knowing about each other |
| Tools behind a registry | Nodes are decoupled from tool implementations — swap without touching node code |
| `interrupt()` for HITL | Graph state is frozen in Redis — resume after hours/days without losing context |
| Distributed locks for execution | Prevents duplicate remediation when multiple agent instances run concurrently |
| `return_exceptions=True` in gather | One failing tool doesn't abort the entire detection phase |
| YAML as lowest-priority config | Env vars always win — containers get the right config without touching YAML |
| Two separate Prometheus instances | Docker Compose Prometheus scrapes the agent; in-cluster Prometheus scrapes k8s |
| `redis-stack-server` not plain redis | LangGraph checkpointer requires RediSearch for checkpoint indexing |

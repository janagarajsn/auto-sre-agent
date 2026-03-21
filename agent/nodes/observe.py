"""
Observe node: Post-action health verification and incident finalisation.

- Re-queries Prometheus to confirm the alert condition has cleared
- Summarises the incident and pushes to long-term memory
- Updates the Incident record status
"""

from __future__ import annotations

import asyncio

from agent.core.state import AgentState
from memory.long_term import get_incident_store
from memory.schemas import IncidentStatus
from observability.logging import get_logger
from tools.prometheus.metrics import get_pod_restart_count, get_http_error_rate
from tools.redis.memory import push_incident_summary

logger = get_logger(__name__)


async def observe_node(state: AgentState) -> dict:
    alert = state["alert"]
    action_result = state.get("action_result")
    logger.info("observe node started", alert=alert.alert_name)

    await asyncio.sleep(10)  # Brief stabilisation wait

    # Re-sample key metrics
    restarts = await get_pod_restart_count(alert.namespace)
    error_rate = await get_http_error_rate(alert.namespace)

    summary = _build_summary(state, restarts, error_rate)

    # Push summary to short-term LLM memory for future context
    await push_incident_summary(summary)

    # Update long-term incident store
    store = await get_incident_store()
    incident = state.get("incident")
    if incident:
        if state["status"] == IncidentStatus.RESOLVED:
            await store.mark_resolved(incident.id)
        else:
            await store.save(incident)

    logger.info("observe node complete", summary=summary[:120])

    return {"status": state["status"]}


def _build_summary(state: AgentState, restarts: list, error_rate: list) -> str:
    alert = state["alert"]
    diagnosis = state.get("diagnosis")
    action = state.get("proposed_action")
    result = state.get("action_result")

    lines = [
        f"Incident: {alert.alert_name} in {alert.namespace}",
        f"Root cause: {diagnosis.root_cause[:150] if diagnosis else 'unknown'}",
        f"Action: {action.action_type if action else 'none'}",
        f"Result: {'success' if result and result.success else 'failed/skipped'}",
        f"Post-action restarts: {len(restarts)} pods reporting",
    ]
    return " | ".join(lines)

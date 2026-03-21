"""
Agent entrypoint.

Initialises all dependencies and exposes run_incident() for the API layer.
Also provides a CLI entrypoint for manual trigger via `sre-agent` command.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from memory.checkpointer import build_checkpointer
from memory.long_term import get_incident_store
from memory.schemas import AlertSignal, Incident, IncidentStatus, Severity
from agent.core.state import AgentState
from agent.workflows.sre_graph import build_sre_graph
from observability.logging import get_logger
from tools.base import register_all_tools

logger = get_logger(__name__)


async def run_incident(alert: AlertSignal) -> Incident:
    """
    Primary entry point for processing an alert through the SRE graph.

    Called by:
    - api/routes/alerts.py (webhook trigger)
    - scripts/simulate_incident.py (manual trigger)
    """
    incident_id = uuid.uuid4()
    thread_id = str(uuid.uuid4())

    incident = Incident(id=incident_id, alert=alert, thread_id=thread_id)

    store = await get_incident_store()
    await store.save(incident)

    initial_state: AgentState = {
        "incident_id": incident_id,
        "thread_id": thread_id,
        "alert": alert,
        "diagnosis": None,
        "proposed_action": None,
        "approval_request": None,
        "action_result": None,
        "status": IncidentStatus.OPEN,
        "requires_approval": False,
        "error": None,
        "messages": [],
        "raw_metrics": {},
        "raw_logs": [],
        "raw_k8s_events": [],
        "incident": incident,
    }

    config = {"configurable": {"thread_id": thread_id}}

    logger.info("agent run started", incident_id=str(incident_id), alert=alert.alert_name)

    async with build_checkpointer() as checkpointer:
        graph = build_sre_graph(checkpointer=checkpointer)

        async for event in graph.astream(initial_state, config=config):
            node_name = next(iter(event), "unknown")
            logger.debug("node complete", node=node_name)

        final_state = await graph.aget_state(config)
        final_incident = final_state.values.get("incident", incident)
        # Sync pipeline outputs back onto the incident record
        final_incident.status = final_state.values.get("status", final_incident.status)
        final_incident.diagnosis = final_state.values.get("diagnosis", final_incident.diagnosis)
        final_incident.proposed_action = final_state.values.get("proposed_action", final_incident.proposed_action)
        final_incident.action_result = final_state.values.get("action_result", final_incident.action_result)

    await store.save(final_incident)

    logger.info(
        "agent run complete",
        incident_id=str(incident_id),
        status=final_incident.status,
    )
    return final_incident


async def resume_incident(thread_id: str, approval_data: dict[str, Any]) -> Incident:
    """
    Resume a graph that was suspended at the approve node.
    Called by api/routes/approvals.py after a human submits a decision.
    """
    config = {"configurable": {"thread_id": thread_id}}

    async with build_checkpointer() as checkpointer:
        graph = build_sre_graph(checkpointer=checkpointer)
        await graph.aupdate_state(config, approval_data)

        async for _ in graph.astream(None, config=config):
            pass

        final_state = await graph.aget_state(config)
        return final_state.values.get("incident")


def main() -> None:
    """CLI entrypoint: trigger a synthetic alert for testing."""
    import argparse

    parser = argparse.ArgumentParser(description="Run the SRE agent manually")
    parser.add_argument("--alert", default="HighCpuUsage")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--severity", default="high")
    args = parser.parse_args()

    register_all_tools()

    alert = AlertSignal(
        alert_name=args.alert,
        severity=Severity(args.severity),
        namespace=args.namespace,
        labels={"env": "dev"},
    )

    asyncio.run(run_incident(alert))


if __name__ == "__main__":
    main()

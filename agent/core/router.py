"""
Conditional edge logic for the LangGraph SRE graph.

Each function here is passed as the `condition` argument to
`graph.add_conditional_edges(...)`. They inspect AgentState and return
the name of the next node to route to.
"""

from __future__ import annotations

from memory.schemas import IncidentStatus
from agent.core.state import AgentState


def route_after_diagnose(state: AgentState) -> str:
    """After diagnosis, skip to observe if confidence is too low to act."""
    diagnosis = state.get("diagnosis")
    if diagnosis is None or diagnosis.confidence < 0.5:
        return "observe"
    return "plan"


def route_after_plan(state: AgentState) -> str:
    """After planning, gate on whether the action requires human approval."""
    action = state.get("proposed_action")
    if action is None:
        return "observe"
    if action.requires_approval:
        return "approve"
    return "execute"


def route_after_approve(state: AgentState) -> str:
    """After approval gate, proceed or abort based on human decision."""
    approval = state.get("approval_request")
    if approval is None:
        return "observe"
    if approval.approved is True:
        return "execute"
    # Rejected or timed out
    return "observe"


def route_after_execute(state: AgentState) -> str:
    """After execution, always proceed to observe for post-action health check."""
    return "observe"


def route_after_detect(state: AgentState) -> str:
    """After detection, skip the pipeline if the alert is a false positive."""
    if state.get("error"):
        return "observe"
    return "diagnose"

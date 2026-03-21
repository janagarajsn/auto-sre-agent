"""
In-graph working memory: the AgentState TypedDict that flows through LangGraph nodes.

Each node receives this state, optionally mutates fields, and returns the delta.
LangGraph merges deltas back into the canonical state via registered reducers.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from langgraph.graph.message import add_messages
from typing_extensions import TypedDict

from memory.schemas import (
    ActionResult,
    AlertSignal,
    ApprovalRequest,
    DiagnosisResult,
    Incident,
    IncidentStatus,
    ProposedAction,
)


class AgentState(TypedDict):
    # Incident identity
    incident_id: UUID
    thread_id: str

    # Input signal
    alert: AlertSignal

    # Pipeline outputs (populated by successive nodes)
    diagnosis: DiagnosisResult | None
    proposed_action: ProposedAction | None
    approval_request: ApprovalRequest | None
    action_result: ActionResult | None

    # Control flow
    status: IncidentStatus
    requires_approval: bool
    error: str | None

    # LLM message history (append-only via add_messages reducer)
    messages: Annotated[list[Any], add_messages]

    # Raw data fetched by tools (intermediate, not persisted long-term)
    raw_metrics: dict[str, Any]
    raw_logs: list[str]
    raw_k8s_events: list[dict[str, Any]]

    # Full incident record (synced to long-term store after each node)
    incident: Incident

"""
Pydantic domain models shared across agent, memory, and API layers.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    DIAGNOSING = "diagnosing"
    PLANNED = "planned"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    RESOLVED = "resolved"
    FAILED = "failed"


class ActionType(StrEnum):
    RESTART_POD = "restart_pod"
    SCALE_DEPLOYMENT = "scale_deployment"
    ROLLBACK_DEPLOYMENT = "rollback_deployment"
    CORDON_NODE = "cordon_node"
    DELETE_POD = "delete_pod"
    NOOP = "noop"


class AlertSignal(BaseModel):
    """Raw alert payload received from Alertmanager or synthetic trigger."""

    alert_name: str
    severity: Severity
    namespace: str
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    starts_at: datetime = Field(default_factory=datetime.utcnow)
    generator_url: str = ""


class DiagnosisResult(BaseModel):
    """Root cause analysis produced by the diagnose node."""

    summary: str
    root_cause: str
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_metrics: list[dict[str, Any]] = Field(default_factory=list)
    supporting_logs: list[str] = Field(default_factory=list)


class ProposedAction(BaseModel):
    """A concrete remediation action proposed by the plan node."""

    id: UUID = Field(default_factory=uuid4)
    action_type: ActionType
    target_namespace: str
    target_resource: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    rationale: str
    requires_approval: bool = True
    risk_level: Severity = Severity.MEDIUM


class ApprovalRequest(BaseModel):
    """Persisted approval gate for human-in-the-loop."""

    id: UUID = Field(default_factory=uuid4)
    incident_id: UUID
    proposed_action: ProposedAction
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime
    approved: bool | None = None  # None = pending
    reviewer: str = ""
    reviewer_notes: str = ""


class ActionResult(BaseModel):
    """Outcome of an executed action."""

    action_id: UUID
    success: bool
    output: str = ""
    error: str = ""
    executed_at: datetime = Field(default_factory=datetime.utcnow)


class Incident(BaseModel):
    """Full lifecycle record for a single SRE incident."""

    id: UUID = Field(default_factory=uuid4)
    status: IncidentStatus = IncidentStatus.OPEN
    alert: AlertSignal
    diagnosis: DiagnosisResult | None = None
    proposed_action: ProposedAction | None = None
    approval: ApprovalRequest | None = None
    action_result: ActionResult | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None
    thread_id: str = ""  # LangGraph checkpoint thread ID

"""
Shared pytest fixtures.
"""

from __future__ import annotations

import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from memory.schemas import (
    AlertSignal,
    DiagnosisResult,
    Incident,
    IncidentStatus,
    ProposedAction,
    ActionType,
    Severity,
)
from memory.short_term import AgentState


@pytest.fixture
def sample_alert() -> AlertSignal:
    return AlertSignal(
        alert_name="PodCrashLooping",
        severity=Severity.HIGH,
        namespace="default",
        labels={"pod": "my-app-abc123", "container": "app"},
        annotations={"summary": "Pod is crash looping"},
        starts_at=datetime.utcnow(),
    )


@pytest.fixture
def sample_diagnosis() -> DiagnosisResult:
    return DiagnosisResult(
        summary="Pod is OOMKilled repeatedly",
        root_cause="Container exceeds memory limit of 256Mi due to memory leak in request handler.",
        confidence=0.92,
        supporting_metrics=[{"metric": "container_restarts", "value": "12"}],
        supporting_logs=["OOMKilled: container exceeded memory limit"],
    )


@pytest.fixture
def sample_action() -> ProposedAction:
    return ProposedAction(
        action_type=ActionType.RESTART_POD,
        target_namespace="default",
        target_resource="my-app-abc123",
        rationale="Pod is OOMKilled — restart to allow rescheduling on node with more memory.",
        requires_approval=False,
        risk_level=Severity.LOW,
    )


@pytest.fixture
def sample_incident(sample_alert) -> Incident:
    return Incident(
        id=uuid4(),
        alert=sample_alert,
        status=IncidentStatus.OPEN,
        thread_id=str(uuid4()),
    )


@pytest.fixture
def base_agent_state(sample_alert, sample_incident) -> AgentState:
    return {
        "incident_id": sample_incident.id,
        "thread_id": sample_incident.thread_id,
        "alert": sample_alert,
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
        "incident": sample_incident,
    }


@pytest.fixture
def mock_redis():
    client = AsyncMock()
    client.ping.return_value = True
    client.get.return_value = None
    client.set.return_value = True
    return client

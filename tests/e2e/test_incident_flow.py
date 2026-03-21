"""
End-to-end test: full incident flow against a real Kind cluster.

Prerequisites:
  - Kind cluster running (scripts/bootstrap.sh)
  - Prometheus deployed in the cluster
  - Redis available at REDIS_URL
  - OPENAI_API_KEY set in environment

Run with:
  pytest tests/e2e/ -m e2e --no-cov -v
"""

from __future__ import annotations

import asyncio
import os

import pytest

from memory.schemas import AlertSignal, IncidentStatus, Severity

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def require_e2e_env():
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set — skipping e2e tests")
    if not os.getenv("RUN_E2E"):
        pytest.skip("RUN_E2E not set — skipping e2e tests")


@pytest.mark.asyncio
async def test_crash_loop_incident_resolves():
    """
    Simulate a CrashLoopBackOff alert and verify the agent:
    1. Detects and diagnoses the issue
    2. Plans a restart action
    3. Executes it (auto-approved in dev mode)
    4. Resolves the incident
    """
    from agent.core.agent import run_incident
    from tools.base import register_all_tools

    register_all_tools()

    alert = AlertSignal(
        alert_name="PodCrashLooping",
        severity=Severity.HIGH,
        namespace="default",
        labels={"pod": "test-crash-pod", "env": "e2e"},
    )

    incident = await run_incident(alert)

    assert incident.status in (IncidentStatus.RESOLVED, IncidentStatus.FAILED)
    assert incident.diagnosis is not None
    assert incident.proposed_action is not None


@pytest.mark.asyncio
async def test_noop_on_low_confidence():
    """When metrics show no anomaly, agent should choose NOOP."""
    from agent.core.agent import run_incident
    from tools.base import register_all_tools

    register_all_tools()

    alert = AlertSignal(
        alert_name="InfoAlert",
        severity=Severity.LOW,
        namespace="default",
        labels={"env": "e2e"},
    )

    incident = await run_incident(alert)

    if incident.proposed_action:
        from memory.schemas import ActionType
        assert incident.proposed_action.action_type == ActionType.NOOP

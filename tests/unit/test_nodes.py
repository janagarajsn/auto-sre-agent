"""
Unit tests for individual graph nodes.
All external I/O is mocked.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from memory.schemas import IncidentStatus, ActionType


class TestDetectNode:
    @pytest.mark.asyncio
    async def test_detect_populates_metrics(self, base_agent_state):
        mock_metrics = [{"metric": {"pod": "my-app"}, "value": [0, "45.2"]}]

        with (
            patch("agent.nodes.detect.get_cpu_usage", AsyncMock(return_value=mock_metrics)),
            patch("agent.nodes.detect.get_memory_usage", AsyncMock(return_value=[])),
            patch("agent.nodes.detect.get_pod_restart_count", AsyncMock(return_value=[])),
            patch("agent.nodes.detect.get_http_error_rate", AsyncMock(return_value=[])),
            patch("agent.nodes.detect.list_recent_events", AsyncMock(return_value=[])),
            patch("agent.nodes.detect.list_pods", AsyncMock(return_value=[])),
        ):
            from agent.nodes.detect import detect_node
            result = await detect_node(base_agent_state)

        assert result["status"] == IncidentStatus.DIAGNOSING
        assert result["raw_metrics"]["cpu"] == mock_metrics

    @pytest.mark.asyncio
    async def test_detect_handles_partial_failures(self, base_agent_state):
        """Tool failures should not crash the node — they return empty lists."""
        with (
            patch("agent.nodes.detect.get_cpu_usage", AsyncMock(side_effect=Exception("timeout"))),
            patch("agent.nodes.detect.get_memory_usage", AsyncMock(return_value=[])),
            patch("agent.nodes.detect.get_pod_restart_count", AsyncMock(return_value=[])),
            patch("agent.nodes.detect.get_http_error_rate", AsyncMock(return_value=[])),
            patch("agent.nodes.detect.list_recent_events", AsyncMock(return_value=[])),
            patch("agent.nodes.detect.list_pods", AsyncMock(return_value=[])),
        ):
            from agent.nodes.detect import detect_node
            result = await detect_node(base_agent_state)

        assert result["raw_metrics"]["cpu"] == []


class TestPlanNode:
    @pytest.mark.asyncio
    async def test_plan_marks_high_risk_for_approval(self, base_agent_state, sample_diagnosis):
        base_agent_state["diagnosis"] = sample_diagnosis

        mock_response = MagicMock()
        mock_response.content = '''```json
{
  "action_type": "rollback_deployment",
  "target_namespace": "default",
  "target_resource": "my-app",
  "parameters": {},
  "rationale": "Rollback to stable revision",
  "requires_approval": true,
  "risk_level": "high"
}
```'''

        with patch("agent.nodes.plan.ChatOpenAI") as MockLLM:
            MockLLM.return_value.ainvoke = AsyncMock(return_value=mock_response)
            from agent.nodes.plan import plan_node
            result = await plan_node(base_agent_state)

        assert result["proposed_action"].action_type == ActionType.ROLLBACK_DEPLOYMENT
        assert result["proposed_action"].requires_approval is True

    @pytest.mark.asyncio
    async def test_plan_falls_back_to_noop_on_parse_error(self, base_agent_state, sample_diagnosis):
        base_agent_state["diagnosis"] = sample_diagnosis

        mock_response = MagicMock()
        mock_response.content = "I cannot determine the right action."

        with patch("agent.nodes.plan.ChatOpenAI") as MockLLM:
            MockLLM.return_value.ainvoke = AsyncMock(return_value=mock_response)
            from agent.nodes.plan import plan_node
            result = await plan_node(base_agent_state)

        assert result["proposed_action"].action_type == ActionType.NOOP


class TestExecuteNode:
    @pytest.mark.asyncio
    async def test_execute_noop_returns_resolved(self, base_agent_state, sample_action):
        sample_action.action_type = ActionType.NOOP
        base_agent_state["proposed_action"] = sample_action

        from agent.nodes.execute import execute_node
        result = await execute_node(base_agent_state)

        assert result["action_result"].success is True
        assert result["status"] == IncidentStatus.RESOLVED

    @pytest.mark.asyncio
    async def test_execute_acquires_lock(self, base_agent_state, sample_action):
        base_agent_state["proposed_action"] = sample_action

        mock_tool_result = MagicMock()
        mock_tool_result.success = True
        mock_tool_result.data = {"pod": "my-app-abc123"}
        mock_tool_result.error = ""

        with (
            patch("agent.nodes.execute.ToolRegistry.get") as mock_registry,
            patch("agent.nodes.execute.action_lock") as mock_lock,
        ):
            mock_registry.return_value.run = AsyncMock(return_value=mock_tool_result)
            mock_lock.return_value.__aenter__ = AsyncMock(return_value=True)
            mock_lock.return_value.__aexit__ = AsyncMock(return_value=False)

            from agent.nodes.execute import execute_node
            result = await execute_node(base_agent_state)

        assert result["action_result"].success is True

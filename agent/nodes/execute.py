"""
Execute node: Dispatches the approved ProposedAction to the appropriate tool.

Uses distributed locking to prevent duplicate execution when multiple
agent instances are running concurrently.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from agent.core.state import AgentState
from memory.schemas import ActionResult, ActionType, IncidentStatus
from observability.logging import get_logger
from tools.base import ToolRegistry
from tools.redis.locks import action_lock

logger = get_logger(__name__)

_ACTION_TO_TOOL: dict[ActionType, str] = {
    ActionType.RESTART_POD: "k8s_restart_pod",
    ActionType.SCALE_DEPLOYMENT: "k8s_scale_deployment",
    ActionType.ROLLBACK_DEPLOYMENT: "k8s_rollback_deployment",
}


async def execute_node(state: AgentState) -> dict:
    action = state["proposed_action"]

    if action.action_type == ActionType.NOOP:
        logger.info("execute node: NOOP, skipping")
        return {
            "action_result": ActionResult(
                action_id=action.id,
                success=True,
                output="NOOP — no action taken",
            ),
            "status": IncidentStatus.RESOLVED,
        }

    lock_resource = f"{action.action_type}:{action.target_namespace}:{action.target_resource}"

    async with action_lock(lock_resource) as acquired:
        if not acquired:
            logger.warning("execute node: action already locked by another instance", resource=lock_resource)
            return {
                "action_result": ActionResult(
                    action_id=action.id,
                    success=False,
                    error="Action is already being executed by another agent instance",
                ),
                "status": IncidentStatus.FAILED,
            }

        tool_name = _ACTION_TO_TOOL.get(action.action_type)
        if not tool_name:
            return {
                "action_result": ActionResult(
                    action_id=action.id,
                    success=False,
                    error=f"No tool registered for action type: {action.action_type}",
                ),
                "status": IncidentStatus.FAILED,
            }

        tool = ToolRegistry.get(tool_name)
        logger.info("execute node dispatching", tool=tool_name, resource=action.target_resource)

        result = await tool.run(
            namespace=action.target_namespace,
            **{_resource_param(action.action_type): action.target_resource},
            **action.parameters,
        )

    action_result = ActionResult(
        action_id=action.id,
        success=result.success,
        output=str(result.data) if result.data else "",
        error=result.error,
        executed_at=datetime.utcnow(),
    )

    logger.info(
        "execute node complete",
        success=result.success,
        action=action.action_type,
        error=result.error or None,
    )

    return {
        "action_result": action_result,
        "status": IncidentStatus.RESOLVED if result.success else IncidentStatus.FAILED,
    }


def _resource_param(action_type: ActionType) -> str:
    if action_type == ActionType.RESTART_POD:
        return "pod_name"
    if action_type in (ActionType.SCALE_DEPLOYMENT, ActionType.ROLLBACK_DEPLOYMENT):
        return "deployment_name"
    return "resource_name"

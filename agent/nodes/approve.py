"""
Approve node: Human-in-the-loop gate.

The graph is compiled with interrupt_before=["approve"], so this node only
runs after a human POSTs to /approvals/{id}. The submit_approval endpoint
writes the decision into Redis under sre:approval:{id}. This node reads it
from Redis directly — no aupdate_state needed, so the full graph state is
preserved.
"""

from __future__ import annotations

from agent.core.state import AgentState
from memory.schemas import ApprovalRequest, IncidentStatus
from observability.logging import get_logger
from tools.redis.client import get_redis_client

logger = get_logger(__name__)

_APPROVAL_KEY_PREFIX = "sre:approval"


async def approve_node(state: AgentState) -> dict:
    approval_request = state.get("approval_request")

    if approval_request is None:
        logger.warning("approve node reached with no approval_request in state")
        return {"status": IncidentStatus.FAILED}

    # Read the updated decision from Redis (written by submit_approval endpoint)
    redis = get_redis_client()
    raw = await redis.get(f"{_APPROVAL_KEY_PREFIX}:{approval_request.id}")

    if not raw:
        logger.warning("approval record not found or expired", approval_id=str(approval_request.id))
        return {"status": IncidentStatus.FAILED}

    updated = ApprovalRequest.model_validate_json(raw)

    if updated.approved is None:
        logger.warning("approve node reached but no decision recorded yet")
        return {"status": IncidentStatus.FAILED}

    logger.info(
        "approve node resolved",
        approved=updated.approved,
        reviewer=updated.reviewer,
    )
    return {
        "approval_request": updated,
        "status": IncidentStatus.EXECUTING if updated.approved else IncidentStatus.FAILED,
    }

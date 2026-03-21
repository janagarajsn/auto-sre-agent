"""
Approve node: Human-in-the-loop gate.

When an action requires approval:
1. Persists an ApprovalRequest to Redis with a UUID
2. Calls langgraph.types.interrupt() to suspend the graph
3. The API layer resumes the graph when a human POSTs to /approvals/{id}

If approval_request is already populated (resumed after interrupt),
this node simply validates the decision and passes through.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from langgraph.types import interrupt

from agent.core.state import AgentState
from configs.settings import get_settings
from memory.schemas import ApprovalRequest, IncidentStatus
from observability.logging import get_logger
from tools.redis.client import get_redis_client

logger = get_logger(__name__)

_APPROVAL_KEY_PREFIX = "sre:approval"


async def approve_node(state: AgentState) -> dict:
    settings = get_settings()
    approval = state.get("approval_request")

    # --- Resumed path: approval decision already recorded ---
    if approval is not None and approval.approved is not None:
        logger.info(
            "approve node resumed",
            approved=approval.approved,
            reviewer=approval.reviewer,
        )
        status = IncidentStatus.EXECUTING if approval.approved else IncidentStatus.FAILED
        return {"status": status}

    # --- First pass: create and persist the approval request ---
    action = state["proposed_action"]
    incident_id = state["incident_id"]
    timeout = settings.approval_timeout_seconds

    approval_request = ApprovalRequest(
        id=uuid4(),
        incident_id=incident_id,
        proposed_action=action,
        expires_at=datetime.utcnow() + timedelta(seconds=timeout),
    )

    redis = get_redis_client()
    key = f"{_APPROVAL_KEY_PREFIX}:{approval_request.id}"
    await redis.set(key, approval_request.model_dump_json(), ex=timeout + 60)

    logger.info(
        "approve node suspended",
        approval_id=str(approval_request.id),
        action=action.action_type,
        expires_in_seconds=timeout,
    )

    # Suspend graph execution — resume via /approvals/{id} endpoint
    interrupt({
        "type": "approval_required",
        "approval_id": str(approval_request.id),
        "action": action.model_dump(mode="json"),
        "expires_at": approval_request.expires_at.isoformat(),
    })

    # Unreachable until graph is resumed
    return {
        "approval_request": approval_request,
        "status": IncidentStatus.AWAITING_APPROVAL,
    }

"""
POST /approvals/{id} — Human approval submission endpoint.

When the graph is suspended at the approve node, an ApprovalRequest is stored
in Redis. This endpoint accepts the human decision and resumes the graph.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from agent.core.agent import resume_incident
from api.middleware.auth import require_api_key
from api.schemas.approval import ApprovalDecision
from memory.schemas import ApprovalRequest
from tools.redis.client import get_redis_client

router = APIRouter()

_APPROVAL_KEY_PREFIX = "sre:approval"


@router.post("/{approval_id}", dependencies=[Depends(require_api_key)])
async def submit_approval(
    approval_id: UUID,
    decision: ApprovalDecision,
) -> dict:
    """
    Submit a human approval decision for a pending action.
    This will resume the suspended LangGraph run.
    """
    redis = get_redis_client()
    key = f"{_APPROVAL_KEY_PREFIX}:{approval_id}"
    raw = await redis.get(key)

    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval request {approval_id} not found or expired",
        )

    approval = ApprovalRequest.model_validate_json(raw)

    # Mutate the approval record with the decision
    approval.approved = decision.approved
    approval.reviewer = decision.reviewer
    approval.reviewer_notes = decision.notes

    # Persist the updated record
    await redis.set(key, approval.model_dump_json(), ex=3600)

    # Resume the graph with the updated approval
    incident = await resume_incident(
        thread_id=str(approval.incident_id),
        approval_data={"approval_request": approval},
    )

    return {
        "approval_id": str(approval_id),
        "approved": decision.approved,
        "incident_status": incident.status if incident else "unknown",
    }


@router.get("/{approval_id}", dependencies=[Depends(require_api_key)])
async def get_approval_status(approval_id: UUID) -> dict:
    """Retrieve the current state of an approval request."""
    redis = get_redis_client()
    raw = await redis.get(f"{_APPROVAL_KEY_PREFIX}:{approval_id}")
    if not raw:
        raise HTTPException(status_code=404, detail="Approval request not found or expired")
    approval = ApprovalRequest.model_validate_json(raw)
    return approval.model_dump(mode="json")

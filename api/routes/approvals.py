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
from memory.long_term import get_incident_store
from memory.schemas import ApprovalRequest
from tools.redis.client import get_redis_client

router = APIRouter()

_APPROVAL_KEY_PREFIX = "sre:approval"


@router.get("/pending", dependencies=[Depends(require_api_key)])
async def list_pending_approvals() -> list[dict]:
    """
    List all approval requests currently waiting for a human decision.
    Returns approval_id, action details, incident_id, and expiry time.
    Use the approval_id to POST to /approvals/{approval_id} with your decision.
    """
    redis = get_redis_client()
    keys = await redis.keys(f"{_APPROVAL_KEY_PREFIX}:*")
    pending = []
    for key in keys:
        raw = await redis.get(key)
        if raw:
            approval = ApprovalRequest.model_validate_json(raw)
            if approval.approved is None:  # still pending
                pending.append({
                    "approval_id": str(approval.id),
                    "incident_id": str(approval.incident_id),
                    "action_type": approval.proposed_action.action_type,
                    "target_resource": approval.proposed_action.target_resource,
                    "target_namespace": approval.proposed_action.target_namespace,
                    "rationale": approval.proposed_action.rationale,
                    "risk_level": approval.proposed_action.risk_level,
                    "expires_at": approval.expires_at.isoformat(),
                    "created_at": approval.created_at.isoformat(),
                })
    return sorted(pending, key=lambda x: x["created_at"], reverse=True)


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

    # Look up the incident to get its thread_id (different from incident_id)
    store = await get_incident_store()
    incident_record = await store.get(approval.incident_id)
    if not incident_record or not incident_record.thread_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Incident {approval.incident_id} not found",
        )

    # Resume the graph using the correct LangGraph thread_id
    incident = await resume_incident(
        thread_id=incident_record.thread_id,
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

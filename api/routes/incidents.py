"""
GET /incidents — Incident history and status endpoints.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from api.middleware.auth import require_api_key
from memory.long_term import get_incident_store
from memory.schemas import Incident, IncidentStatus

router = APIRouter()


@router.get("/", dependencies=[Depends(require_api_key)])
async def list_incidents(limit: int = 20) -> list[dict]:
    store = await get_incident_store()
    incidents = await store.list_recent(limit=limit)
    return [i.model_dump(mode="json") for i in incidents]


@router.get("/pending-approvals", dependencies=[Depends(require_api_key)])
async def list_pending_approvals() -> list[dict]:
    store = await get_incident_store()
    incidents = await store.list_by_status(IncidentStatus.AWAITING_APPROVAL)
    return [i.model_dump(mode="json") for i in incidents]


@router.get("/{incident_id}", dependencies=[Depends(require_api_key)])
async def get_incident(incident_id: UUID) -> dict:
    store = await get_incident_store()
    incident = await store.get(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident.model_dump(mode="json")

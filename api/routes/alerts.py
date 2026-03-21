"""
POST /alerts — Alertmanager webhook receiver.

Alertmanager sends a webhook payload when an alert fires.
This route parses it, converts it to an AlertSignal, and triggers the agent.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from agent.core.agent import run_incident
from api.middleware.auth import require_api_key
from api.schemas.alert import AlertmanagerPayload, AlertmanagerAlert
from memory.schemas import AlertSignal, Severity

router = APIRouter()


def _parse_severity(labels: dict) -> Severity:
    raw = labels.get("severity", "medium").lower()
    try:
        return Severity(raw)
    except ValueError:
        return Severity.MEDIUM


def _alertmanager_to_signal(alert: AlertmanagerAlert) -> AlertSignal:
    return AlertSignal(
        alert_name=alert.labels.get("alertname", "UnknownAlert"),
        severity=_parse_severity(alert.labels),
        namespace=alert.labels.get("namespace", "default"),
        labels=alert.labels,
        annotations=alert.annotations,
        starts_at=alert.startsAt or datetime.utcnow(),
        generator_url=alert.generatorURL or "",
    )


@router.post("/", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_api_key)])
async def receive_alertmanager_webhook(
    payload: AlertmanagerPayload,
    background_tasks: BackgroundTasks,
) -> dict:
    """
    Receive Alertmanager webhook and dispatch each firing alert to the SRE agent.
    Returns immediately (202 Accepted); agent runs in background.
    """
    firing_alerts = [a for a in payload.alerts if a.status == "firing"]
    if not firing_alerts:
        return {"message": "No firing alerts in payload", "dispatched": 0}

    for alert in firing_alerts:
        signal = _alertmanager_to_signal(alert)
        background_tasks.add_task(run_incident, signal)

    return {"message": "Alerts dispatched", "dispatched": len(firing_alerts)}


@router.post("/test", status_code=status.HTTP_200_OK, dependencies=[Depends(require_api_key)])
async def trigger_test_alert(signal: AlertSignal) -> dict:
    """Synchronous test endpoint — runs the agent and returns the result."""
    incident = await run_incident(signal)
    return {"incident_id": str(incident.id), "status": incident.status}

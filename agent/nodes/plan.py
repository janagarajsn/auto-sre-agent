"""
Plan node: Translates diagnosis into a concrete, typed ProposedAction.

Input:  diagnosis (DiagnosisResult), alert (AlertSignal)
Output: proposed_action (ProposedAction)
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from uuid import uuid4

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from agent.core.state import AgentState
from agent.prompts import load_prompt
from configs.settings import get_settings
from memory.long_term import get_incident_store
from memory.schemas import ActionType, ApprovalRequest, IncidentStatus, ProposedAction, Severity
from observability.logging import get_logger
from tools.redis.client import get_redis_client

logger = get_logger(__name__)

# Actions that always require human approval in production
_HIGH_RISK_ACTIONS = {ActionType.ROLLBACK_DEPLOYMENT, ActionType.CORDON_NODE}

# Actions that auto-approve in non-prod environments
_LOW_RISK_ACTIONS = {ActionType.RESTART_POD, ActionType.NOOP}


async def plan_node(state: AgentState) -> dict:
    settings = get_settings()
    diagnosis = state["diagnosis"]
    alert = state["alert"]
    logger.info("plan node started", root_cause=diagnosis.root_cause[:80])

    llm = ChatOpenAI(
        model=settings.openai_model,
        temperature=0.0,
        api_key=settings.openai_api_key,
    )

    system_prompt = load_prompt("plan")
    user_content = (
        f"## Diagnosis\n{diagnosis.root_cause}\n\n"
        f"## Alert\nNamespace: {alert.namespace}\n"
        f"Severity: {alert.severity}\nLabels: {json.dumps(alert.labels)}"
    )

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ])

    proposed = _parse_action(response.content, alert.namespace)
    proposed.requires_approval = _needs_approval(proposed, settings.is_production)

    logger.info(
        "plan node complete",
        action=proposed.action_type,
        requires_approval=proposed.requires_approval,
    )

    result: dict = {
        "proposed_action": proposed,
        "status": IncidentStatus.AWAITING_APPROVAL if proposed.requires_approval else IncidentStatus.EXECUTING,
        "messages": [response],
    }

    # If approval is required, create and persist the ApprovalRequest NOW —
    # before the graph hits interrupt_before=["approve"] and pauses.
    # This ensures /approvals/pending can surface the approval_id immediately.
    if proposed.requires_approval:
        approval_request = ApprovalRequest(
            id=uuid4(),
            incident_id=state["incident_id"],
            proposed_action=proposed,
            expires_at=datetime.utcnow() + timedelta(seconds=settings.approval_timeout_seconds),
        )
        redis = get_redis_client()
        await redis.set(
            f"sre:approval:{approval_request.id}",
            approval_request.model_dump_json(),
            ex=settings.approval_timeout_seconds + 60,
        )
        # Also write approval into the incident record so the API surfaces it
        store = await get_incident_store()
        incident = await store.get(state["incident_id"])
        if incident:
            incident.approval = approval_request
            incident.status = IncidentStatus.AWAITING_APPROVAL
            await store.save(incident)

        logger.info(
            "approval request created",
            approval_id=str(approval_request.id),
            action=proposed.action_type,
            expires_in_seconds=settings.approval_timeout_seconds,
        )
        result["approval_request"] = approval_request

    return result


def _needs_approval(action: ProposedAction, is_prod: bool) -> bool:
    if action.action_type in _HIGH_RISK_ACTIONS:
        return True
    if action.action_type in _LOW_RISK_ACTIONS and not is_prod:
        return False
    return action.risk_level in (Severity.HIGH, Severity.CRITICAL)


def _parse_action(content: str, namespace: str) -> ProposedAction:
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            return ProposedAction(**data)
        except Exception:
            pass
    # Safe fallback
    return ProposedAction(
        action_type=ActionType.NOOP,
        target_namespace=namespace,
        target_resource="unknown",
        rationale=content[:300],
        requires_approval=True,
    )

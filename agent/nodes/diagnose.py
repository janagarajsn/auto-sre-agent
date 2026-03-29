"""
Diagnose node: Uses the LLM to perform root cause analysis.

Input:  raw_metrics, raw_logs, raw_k8s_events
Output: diagnosis (DiagnosisResult)
"""

from __future__ import annotations

import json

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from configs.settings import get_settings
from observability.logging import get_logger
from agent.core.state import AgentState
from memory.schemas import DiagnosisResult, IncidentStatus
from agent.prompts import load_prompt

logger = get_logger(__name__)


async def diagnose_node(state: AgentState) -> dict:
    settings = get_settings()
    alert = state["alert"]
    logger.info("diagnose node started", alert=alert.alert_name)

    llm = ChatOpenAI(
        model=settings.openai_model,
        temperature=settings.openai_temperature,
        api_key=settings.openai_api_key,
    )

    system_prompt = load_prompt("diagnose")
    user_content = _build_context(state)

    response = await llm.ainvoke([
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ])

    diagnosis = _parse_diagnosis(response.content)
    logger.info(
        "diagnose node complete",
        root_cause=diagnosis.root_cause,
        confidence=diagnosis.confidence,
    )

    return {
        "diagnosis": diagnosis,
        "status": IncidentStatus.PLANNED,
        "messages": [HumanMessage(content=user_content), response],
    }


def _build_context(state: AgentState) -> str:
    alert = state["alert"]
    parts = [
        f"## Alert\nName: {alert.alert_name}\nSeverity: {alert.severity}\n"
        f"Namespace: {alert.namespace}\nLabels: {json.dumps(alert.labels, indent=2)}",
        f"## Kubernetes Events\n{json.dumps(state['raw_k8s_events'][:10], indent=2)}",
        f"## Pod Restart Counts\n{json.dumps(state['raw_metrics'].get('restarts', [])[:10], indent=2)}",
        f"## CPU Usage\n{json.dumps(state['raw_metrics'].get('cpu', [])[:5], indent=2)}",
        f"## Memory Usage\n{json.dumps(state['raw_metrics'].get('memory', [])[:5], indent=2)}",
    ]

    rollout = state["raw_metrics"].get("deployment_rollout", [])
    if rollout:
        parts.append(f"## Deployment Rollout State\n{json.dumps(rollout, indent=2)}")

    if state["raw_logs"]:
        parts.append(f"## Recent Logs\n{''.join(state['raw_logs'][:3])}")

    return "\n\n".join(parts)


def _parse_diagnosis(content: str) -> DiagnosisResult:
    """Parse LLM response. Expects a JSON block or falls back to text."""
    import re
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            return DiagnosisResult(**data)
        except Exception:
            pass
    # Fallback: wrap raw text
    return DiagnosisResult(
        summary=content[:200],
        root_cause=content,
        confidence=0.5,
    )

"""
Detect node: Enriches the raw alert signal with live metric and event data.

Input:  alert (AlertSignal)
Output: raw_metrics, raw_logs, raw_k8s_events populated in AgentState
"""

from __future__ import annotations

import asyncio

from observability.logging import get_logger
from agent.core.state import AgentState
from memory.schemas import IncidentStatus
from tools.kubernetes.deployments import get_deployment_rollout_info
from tools.kubernetes.events import list_recent_events
from tools.kubernetes.pods import get_pod_logs, list_pods
from tools.prometheus.metrics import (
    get_cpu_usage,
    get_memory_usage,
    get_pod_restart_count,
    get_http_error_rate,
)

logger = get_logger(__name__)


async def detect_node(state: AgentState) -> dict:
    alert = state["alert"]
    namespace = alert.namespace
    logger.info("detect node started", alert=alert.alert_name, namespace=namespace)

    # Fetch data in parallel
    cpu_task = get_cpu_usage(namespace)
    mem_task = get_memory_usage(namespace)
    restart_task = get_pod_restart_count(namespace)
    error_rate_task = get_http_error_rate(namespace)
    events_task = list_recent_events(namespace, event_type="Warning")
    pods_task = list_pods(namespace)
    rollout_task = get_deployment_rollout_info(namespace)

    (
        cpu,
        mem,
        restarts,
        error_rate,
        events,
        pods,
        rollout_info,
    ) = await asyncio.gather(
        cpu_task,
        mem_task,
        restart_task,
        error_rate_task,
        events_task,
        pods_task,
        rollout_task,
        return_exceptions=True,
    )

    # Collect logs for high-restart pods
    logs: list[str] = []
    if isinstance(pods, list):
        for pod in pods:
            if pod.get("restarts", 0) >= 3:
                try:
                    log = await get_pod_logs(namespace, pod["name"], tail_lines=50)
                    logs.append(f"=== {pod['name']} ===\n{log}")
                except Exception:
                    pass

    raw_metrics = {
        "cpu": cpu if not isinstance(cpu, Exception) else [],
        "memory": mem if not isinstance(mem, Exception) else [],
        "restarts": restarts if not isinstance(restarts, Exception) else [],
        "error_rate": error_rate if not isinstance(error_rate, Exception) else [],
        "pods": pods if not isinstance(pods, Exception) else [],
        "deployment_rollout": rollout_info if not isinstance(rollout_info, Exception) else [],
    }

    logger.info("detect node complete", metrics_keys=list(raw_metrics.keys()))

    return {
        "raw_metrics": raw_metrics,
        "raw_logs": logs,
        "raw_k8s_events": events if not isinstance(events, Exception) else [],
        "status": IncidentStatus.DIAGNOSING,
    }

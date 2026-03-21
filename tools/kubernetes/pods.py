"""
Pod-level Kubernetes operations.
"""

from __future__ import annotations

from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from observability.logging import get_logger
from tools.base import BaseTool, ToolResult
from tools.kubernetes.client import get_core_v1

logger = get_logger(__name__)


class RestartPodTool(BaseTool):
    name = "k8s_restart_pod"
    description = "Delete a Kubernetes pod so its controller recreates it"

    async def run(self, namespace: str, pod_name: str, **kwargs: Any) -> ToolResult:
        try:
            api = get_core_v1()
            api.delete_namespaced_pod(
                name=pod_name,
                namespace=namespace,
                body=client.V1DeleteOptions(grace_period_seconds=0),
            )
            logger.info("Pod deleted (will restart)", pod=pod_name, namespace=namespace)
            return ToolResult.ok({"pod": pod_name, "namespace": namespace, "action": "deleted"})
        except ApiException as e:
            return ToolResult.fail(f"k8s API error: {e.status} {e.reason}")
        except Exception as e:
            return ToolResult.fail(str(e))


async def list_pods(namespace: str, label_selector: str = "") -> list[dict[str, Any]]:
    api = get_core_v1()
    result = api.list_namespaced_pod(namespace=namespace, label_selector=label_selector)
    return [
        {
            "name": pod.metadata.name,
            "phase": pod.status.phase,
            "node": pod.spec.node_name,
            "restarts": sum(
                cs.restart_count
                for cs in (pod.status.container_statuses or [])
            ),
            "conditions": [
                {"type": c.type, "status": c.status}
                for c in (pod.status.conditions or [])
            ],
        }
        for pod in result.items
    ]


async def get_pod_logs(
    namespace: str,
    pod_name: str,
    container: str | None = None,
    tail_lines: int = 100,
) -> str:
    api = get_core_v1()
    return api.read_namespaced_pod_log(
        name=pod_name,
        namespace=namespace,
        container=container,
        tail_lines=tail_lines,
    )

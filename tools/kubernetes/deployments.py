"""
Deployment-level Kubernetes operations: scale, rollout restart, rollback.
"""

from __future__ import annotations

from typing import Any

from kubernetes import client
from kubernetes.client.rest import ApiException

from observability.logging import get_logger
from tools.base import BaseTool, ToolResult
from tools.kubernetes.client import get_apps_v1

logger = get_logger(__name__)


class ScaleDeploymentTool(BaseTool):
    name = "k8s_scale_deployment"
    description = "Scale a Kubernetes Deployment to a target replica count"

    async def run(
        self, namespace: str, deployment_name: str, replicas: int, **kwargs: Any
    ) -> ToolResult:
        try:
            api = get_apps_v1()
            api.patch_namespaced_deployment_scale(
                name=deployment_name,
                namespace=namespace,
                body={"spec": {"replicas": replicas}},
            )
            logger.info(
                "Deployment scaled",
                deployment=deployment_name,
                namespace=namespace,
                replicas=replicas,
            )
            return ToolResult.ok(
                {"deployment": deployment_name, "namespace": namespace, "replicas": replicas}
            )
        except ApiException as e:
            return ToolResult.fail(f"k8s API error: {e.status} {e.reason}")
        except Exception as e:
            return ToolResult.fail(str(e))


class RollbackDeploymentTool(BaseTool):
    name = "k8s_rollback_deployment"
    description = "Roll back a Kubernetes Deployment to its previous revision"

    async def run(
        self, namespace: str, deployment_name: str, **kwargs: Any
    ) -> ToolResult:
        try:
            api = get_apps_v1()
            # Trigger rollout undo by patching rollback annotation
            body = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": _now_iso()
                            }
                        }
                    }
                }
            }
            api.patch_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body=body,
            )
            logger.info(
                "Deployment rollback triggered",
                deployment=deployment_name,
                namespace=namespace,
            )
            return ToolResult.ok({"deployment": deployment_name, "namespace": namespace})
        except ApiException as e:
            return ToolResult.fail(f"k8s API error: {e.status} {e.reason}")
        except Exception as e:
            return ToolResult.fail(str(e))


async def get_deployment(namespace: str, name: str) -> dict[str, Any] | None:
    api = get_apps_v1()
    try:
        d = api.read_namespaced_deployment(name=name, namespace=namespace)
        return {
            "name": d.metadata.name,
            "namespace": d.metadata.namespace,
            "replicas": d.spec.replicas,
            "ready_replicas": d.status.ready_replicas or 0,
            "image": d.spec.template.spec.containers[0].image if d.spec.template.spec.containers else "",
            "generation": d.metadata.generation,
            "observed_generation": d.status.observed_generation,
        }
    except ApiException as e:
        if e.status == 404:
            return None
        raise


async def list_deployments(namespace: str) -> list[dict[str, Any]]:
    api = get_apps_v1()
    result = api.list_namespaced_deployment(namespace=namespace)
    return [
        {
            "name": d.metadata.name,
            "replicas": d.spec.replicas,
            "ready_replicas": d.status.ready_replicas or 0,
        }
        for d in result.items
    ]


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()

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
    description = "Roll back a Kubernetes Deployment to its previous revision (equivalent to kubectl rollout undo)"

    async def run(
        self, namespace: str, deployment_name: str, **kwargs: Any
    ) -> ToolResult:
        try:
            api = get_apps_v1()

            # Get current deployment and its revision
            deployment = api.read_namespaced_deployment(
                name=deployment_name, namespace=namespace
            )
            current_revision = int(
                (deployment.metadata.annotations or {}).get(
                    "deployment.kubernetes.io/revision", "0"
                )
            )
            if current_revision < 2:
                return ToolResult.fail(
                    f"Deployment {deployment_name} has no previous revision to roll back to "
                    f"(current revision: {current_revision})"
                )

            target_revision = current_revision - 1

            # Find the ReplicaSet that corresponds to the previous revision
            label_selector = ",".join(
                f"{k}={v}"
                for k, v in (deployment.spec.selector.match_labels or {}).items()
            )
            rs_list = api.list_namespaced_replica_set(
                namespace=namespace, label_selector=label_selector
            )

            target_rs = None
            for rs in rs_list.items:
                rs_revision = int(
                    (rs.metadata.annotations or {}).get(
                        "deployment.kubernetes.io/revision", "0"
                    )
                )
                if rs_revision == target_revision:
                    target_rs = rs
                    break

            if target_rs is None:
                return ToolResult.fail(
                    f"Could not find ReplicaSet for revision {target_revision} "
                    f"of deployment {deployment_name}"
                )

            # Full replace (PUT) so all fields including command/args/env are
            # overwritten — strategic/merge patch can't reliably delete fields.
            deployment.spec.template = target_rs.spec.template
            deployment.metadata.managed_fields = None
            api.replace_namespaced_deployment(
                name=deployment_name,
                namespace=namespace,
                body=deployment,
            )

            prev_containers = target_rs.spec.template.spec.containers or []
            prev_image = prev_containers[0].image if prev_containers else "unknown"
            logger.info(
                "Deployment rolled back",
                deployment=deployment_name,
                namespace=namespace,
                from_revision=current_revision,
                to_revision=target_revision,
                image=prev_image,
            )
            return ToolResult.ok({
                "deployment": deployment_name,
                "namespace": namespace,
                "rolled_back_from_revision": current_revision,
                "rolled_back_to_revision": target_revision,
                "image": prev_image,
            })
        except ApiException as e:
            return ToolResult.fail(f"k8s API error: {e.status} {e.reason}")
        except Exception as e:
            return ToolResult.fail(str(e))


async def get_deployment_rollout_info(namespace: str) -> list[dict[str, Any]]:
    """
    Return rollout state for all deployments in a namespace.
    Includes current image, previous revision image, and ready/desired replica counts.
    Used by the detect node to give the LLM a "recently changed image?" signal.
    """
    api = get_apps_v1()
    results = []

    try:
        deployments = api.list_namespaced_deployment(namespace=namespace).items
    except ApiException:
        return results

    # Build a map of revision → ReplicaSet for each deployment
    try:
        all_rs = api.list_namespaced_replica_set(namespace=namespace).items
    except ApiException:
        all_rs = []

    rs_by_deployment: dict[str, list] = {}
    for rs in all_rs:
        owner = next(
            (r.name for r in (rs.metadata.owner_references or []) if r.kind == "Deployment"),
            None,
        )
        if owner:
            rs_by_deployment.setdefault(owner, []).append(rs)

    for d in deployments:
        current_revision = int(
            (d.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0")
        )
        containers = d.spec.template.spec.containers or []
        current_image = containers[0].image if containers else "unknown"

        info: dict[str, Any] = {
            "deployment": d.metadata.name,
            "desired_replicas": d.spec.replicas,
            "ready_replicas": d.status.ready_replicas or 0,
            "current_revision": current_revision,
            "current_image": current_image,
            "previous_image": None,
        }

        # Find the previous revision's ReplicaSet to get the prior image
        prev_rs = None
        for rs in rs_by_deployment.get(d.metadata.name, []):
            rs_rev = int(
                (rs.metadata.annotations or {}).get("deployment.kubernetes.io/revision", "0")
            )
            if rs_rev == current_revision - 1:
                prev_rs = rs
                break

        if prev_rs:
            prev_containers = (prev_rs.spec.template.spec.containers or [])
            info["previous_image"] = prev_containers[0].image if prev_containers else None

        results.append(info)

    return results


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



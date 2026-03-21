"""
Kubernetes cluster event fetcher.
Events provide low-level signals about OOMKills, scheduling failures, etc.
"""

from __future__ import annotations

from typing import Any

from tools.kubernetes.client import get_core_v1


async def list_recent_events(
    namespace: str,
    involved_object_name: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    api = get_core_v1()
    field_selectors = []
    if involved_object_name:
        field_selectors.append(f"involvedObject.name={involved_object_name}")
    if event_type:
        field_selectors.append(f"type={event_type}")

    result = api.list_namespaced_event(
        namespace=namespace,
        field_selector=",".join(field_selectors) if field_selectors else None,
        limit=limit,
    )

    return [
        {
            "name": e.metadata.name,
            "type": e.type,
            "reason": e.reason,
            "message": e.message,
            "object": f"{e.involved_object.kind}/{e.involved_object.name}",
            "count": e.count,
            "first_time": e.first_timestamp.isoformat() if e.first_timestamp else None,
            "last_time": e.last_timestamp.isoformat() if e.last_timestamp else None,
        }
        for e in sorted(
            result.items,
            key=lambda x: x.last_timestamp or x.first_timestamp,
            reverse=True,
        )
    ]

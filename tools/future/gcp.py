"""
GCP integration stub.

Extend BaseTool here when adding GCP-backed actions:
- GKE node pool scaling
- Cloud Monitoring metric fetching
- Cloud Run service restarts
"""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool, ToolResult


class GCPNodePoolScaleTool(BaseTool):
    name = "gcp_nodepool_scale"
    description = "Scale a GKE node pool (stub)"

    async def run(self, **kwargs: Any) -> ToolResult:
        raise NotImplementedError("GCP integration not yet implemented")

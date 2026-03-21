"""
Abstract base class for all SRE agent tools.

Every tool integration (Prometheus, Kubernetes, Redis, future AWS/GCP) must
extend BaseTool. Agent nodes interact with tools exclusively through this
interface, enabling:
- Uniform error handling
- Easy mocking in tests
- Runtime tool registry lookup
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolResult:
    success: bool
    data: Any = None
    error: str = ""

    @classmethod
    def ok(cls, data: Any = None) -> "ToolResult":
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str) -> "ToolResult":
        return cls(success=False, error=error)


class BaseTool(ABC):
    """All tool integrations extend this class."""

    name: str
    description: str

    @abstractmethod
    async def run(self, **kwargs: Any) -> ToolResult:
        ...

    def __repr__(self) -> str:
        return f"<Tool: {self.name}>"


# ---------------------------------------------------------------------------
# Tool Registry
# ---------------------------------------------------------------------------

class ToolRegistry:
    """
    Singleton registry mapping tool names to instances.
    Agent nodes call registry.get("prometheus_query") instead of importing
    tool modules directly, keeping nodes decoupled from implementations.
    """

    _tools: dict[str, BaseTool] = {}

    @classmethod
    def register(cls, tool: BaseTool) -> None:
        cls._tools[tool.name] = tool

    @classmethod
    def get(cls, name: str) -> BaseTool:
        if name not in cls._tools:
            raise KeyError(f"Tool '{name}' is not registered. Available: {list(cls._tools)}")
        return cls._tools[name]

    @classmethod
    def all(cls) -> list[BaseTool]:
        return list(cls._tools.values())


def register_all_tools() -> None:
    """Import and register every built-in tool. Call once at startup."""
    from tools.kubernetes.pods import RestartPodTool
    from tools.kubernetes.deployments import ScaleDeploymentTool, RollbackDeploymentTool
    from tools.prometheus.metrics import QueryMetricsTool
    from tools.prometheus.alerts import FetchAlertsTool

    for tool in [
        RestartPodTool(),
        ScaleDeploymentTool(),
        RollbackDeploymentTool(),
        QueryMetricsTool(),
        FetchAlertsTool(),
    ]:
        ToolRegistry.register(tool)

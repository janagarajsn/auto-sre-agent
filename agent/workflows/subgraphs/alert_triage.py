"""
Alert triage subgraph (stub).

Intended as a fast pre-filter before running the full SRE graph:
- Classify alert type (pod, node, network, application)
- Check if alert is already being handled (dedup via Redis)
- Determine severity override based on business context

Activate by wiring this as a subgraph node in supervisor.py.
"""

from __future__ import annotations

from agent.core.state import AgentState


async def triage_node(state: AgentState) -> dict:
    """
    Lightweight triage: returns early if alert is a known false-positive pattern
    or is already being handled by another incident thread.
    """
    raise NotImplementedError("Alert triage subgraph not yet implemented")

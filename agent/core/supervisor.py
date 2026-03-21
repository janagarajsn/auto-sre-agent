"""
Multi-agent supervisor (optional, for future subgraph delegation).

When the SRE workload grows, this supervisor can spawn specialised subgraphs:
- alert_triage: fast classification before full diagnosis
- rollback_flow: dedicated rollback decision pipeline

Currently unused — the single sre_graph.py handles all cases.
Stub is here to establish the extension point.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from agent.core.state import AgentState


def build_supervisor_graph():
    """
    Placeholder for a supervisor that delegates to sub-agents.
    Extend here when adding specialised subgraphs.
    """
    raise NotImplementedError(
        "Supervisor graph not yet implemented. Use agent/workflows/sre_graph.py directly."
    )

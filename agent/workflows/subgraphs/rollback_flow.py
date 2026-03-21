"""
Rollback decision subgraph (stub).

Dedicated pipeline for rollback decisions — requires additional validation:
- Verify a previous stable revision exists
- Check if the current deployment has any active traffic
- Validate rollback won't violate PDB (PodDisruptionBudget)
- Always requires human approval regardless of settings

Activate by wiring this as a subgraph node in supervisor.py.
"""

from __future__ import annotations

from agent.core.state import AgentState


async def rollback_decision_node(state: AgentState) -> dict:
    raise NotImplementedError("Rollback flow subgraph not yet implemented")

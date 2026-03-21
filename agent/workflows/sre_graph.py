"""
Primary compiled LangGraph StateGraph for the SRE agent.

Graph topology:
  START → detect → diagnose → plan → [approve] → execute → observe → END

Conditional edges determine whether the approve node is inserted
and whether execution proceeds or is skipped.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from agent.core.state import AgentState
from agent.core.router import (
    route_after_detect,
    route_after_diagnose,
    route_after_plan,
    route_after_approve,
    route_after_execute,
)
from agent.nodes.detect import detect_node
from agent.nodes.diagnose import diagnose_node
from agent.nodes.plan import plan_node
from agent.nodes.approve import approve_node
from agent.nodes.execute import execute_node
from agent.nodes.observe import observe_node


def build_sre_graph(checkpointer: BaseCheckpointSaver | None = None):
    """
    Build and compile the SRE agent graph.

    Args:
        checkpointer: Optional LangGraph checkpointer for state persistence.
                      Required for human-in-the-loop approval resumption.

    Returns:
        Compiled LangGraph app ready for ainvoke() / astream().
    """
    graph = StateGraph(AgentState)

    # Register nodes
    graph.add_node("detect", detect_node)
    graph.add_node("diagnose", diagnose_node)
    graph.add_node("plan", plan_node)
    graph.add_node("approve", approve_node)
    graph.add_node("execute", execute_node)
    graph.add_node("observe", observe_node)

    # Entry edge
    graph.add_edge(START, "detect")

    # Conditional edges
    graph.add_conditional_edges(
        "detect",
        route_after_detect,
        {"diagnose": "diagnose", "observe": "observe"},
    )
    graph.add_conditional_edges(
        "diagnose",
        route_after_diagnose,
        {"plan": "plan", "observe": "observe"},
    )
    graph.add_conditional_edges(
        "plan",
        route_after_plan,
        {"approve": "approve", "execute": "execute", "observe": "observe"},
    )
    graph.add_conditional_edges(
        "approve",
        route_after_approve,
        {"execute": "execute", "observe": "observe"},
    )
    graph.add_conditional_edges(
        "execute",
        route_after_execute,
        {"observe": "observe"},
    )

    # Terminal edge
    graph.add_edge("observe", END)

    return graph.compile(
        checkpointer=checkpointer,
        interrupt_before=["approve"],  # Pause before approval node for HITL
    )

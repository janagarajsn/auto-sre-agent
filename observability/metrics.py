"""
Prometheus metrics exposition for the agent itself.

Exposes agent-side operational metrics on /metrics for scraping.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry

REGISTRY = CollectorRegistry()

incidents_total = Counter(
    "sre_agent_incidents_total",
    "Total number of incidents processed",
    ["status", "alert_name"],
    registry=REGISTRY,
)

incidents_in_progress = Gauge(
    "sre_agent_incidents_in_progress",
    "Number of incidents currently being processed",
    registry=REGISTRY,
)

node_duration_seconds = Histogram(
    "sre_agent_node_duration_seconds",
    "Time spent in each graph node",
    ["node_name"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
    registry=REGISTRY,
)

actions_executed_total = Counter(
    "sre_agent_actions_executed_total",
    "Total actions executed by type and outcome",
    ["action_type", "success"],
    registry=REGISTRY,
)

approvals_pending = Gauge(
    "sre_agent_approvals_pending",
    "Number of incidents awaiting human approval",
    registry=REGISTRY,
)

llm_tokens_used_total = Counter(
    "sre_agent_llm_tokens_total",
    "Total LLM tokens consumed",
    ["model", "node_name"],
    registry=REGISTRY,
)

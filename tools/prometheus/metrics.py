"""
Named metric helpers for common SRE signals.
Returns structured data ready for injection into LLM context.
"""

from __future__ import annotations

from typing import Any

from tools.base import BaseTool, ToolResult
from tools.prometheus.client import PrometheusClient


class QueryMetricsTool(BaseTool):
    name = "prometheus_query"
    description = "Execute a raw PromQL query against Prometheus"

    def __init__(self) -> None:
        self._client = PrometheusClient()

    async def run(self, promql: str, **kwargs: Any) -> ToolResult:
        try:
            result = await self._client.query(promql)
            return ToolResult.ok(result)
        except Exception as e:
            return ToolResult.fail(str(e))


# ---------------------------------------------------------------------------
# Convenience helpers used by agent nodes directly
# ---------------------------------------------------------------------------

async def get_cpu_usage(namespace: str, pod: str | None = None) -> list[dict]:
    client = PrometheusClient()
    selector = f'namespace="{namespace}"'
    if pod:
        selector += f', pod="{pod}"'
    return await client.query(
        f'rate(container_cpu_usage_seconds_total{{{selector}}}[5m]) * 100'
    )


async def get_memory_usage(namespace: str, pod: str | None = None) -> list[dict]:
    client = PrometheusClient()
    selector = f'namespace="{namespace}"'
    if pod:
        selector += f', pod="{pod}"'
    return await client.query(
        f'container_memory_working_set_bytes{{{selector}}} / 1024 / 1024'
    )


async def get_pod_restart_count(namespace: str) -> list[dict]:
    client = PrometheusClient()
    return await client.query(
        f'kube_pod_container_status_restarts_total{{namespace="{namespace}"}}'
    )


async def get_http_error_rate(namespace: str, service: str | None = None) -> list[dict]:
    client = PrometheusClient()
    selector = f'namespace="{namespace}"'
    if service:
        selector += f', service="{service}"'
    return await client.query(
        f'rate(http_requests_total{{{selector}, status=~"5.."}}[5m])'
        f' / rate(http_requests_total{{{selector}}}[5m])'
    )


async def get_p99_latency(namespace: str, service: str | None = None) -> list[dict]:
    client = PrometheusClient()
    selector = f'namespace="{namespace}"'
    if service:
        selector += f', service="{service}"'
    return await client.query(
        f'histogram_quantile(0.99, rate(http_request_duration_seconds_bucket{{{selector}}}[5m]))'
    )

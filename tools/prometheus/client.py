"""
Async Prometheus HTTP API client.
Wraps the Prometheus v1 query and query_range endpoints.
"""

from __future__ import annotations

from typing import Any

import aiohttp
from tenacity import retry, stop_after_attempt, wait_exponential

from configs.settings import get_settings
from observability.logging import get_logger

logger = get_logger(__name__)


class PrometheusClient:
    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or get_settings().prometheus_url).rstrip("/")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
    async def query(self, promql: str, time: str | None = None) -> list[dict[str, Any]]:
        """Execute an instant PromQL query. Returns the result list."""
        params: dict[str, str] = {"query": promql}
        if time:
            params["time"] = time

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self._base_url}/api/v1/query",
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json()

        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {payload.get('error', 'unknown')}")

        return payload["data"]["result"]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=5))
    async def query_range(
        self,
        promql: str,
        start: str,
        end: str,
        step: str = "60s",
    ) -> list[dict[str, Any]]:
        """Execute a range PromQL query. Returns the result list."""
        params = {"query": promql, "start": start, "end": end, "step": step}

        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self._base_url}/api/v1/query_range",
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                payload = await resp.json()

        if payload.get("status") != "success":
            raise RuntimeError(f"Prometheus range query failed: {payload.get('error', 'unknown')}")

        return payload["data"]["result"]

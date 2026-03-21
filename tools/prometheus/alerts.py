"""
Fetch and parse active Alertmanager / Prometheus alert rules.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from configs.settings import get_settings
from tools.base import BaseTool, ToolResult


class FetchAlertsTool(BaseTool):
    name = "prometheus_alerts"
    description = "Fetch currently firing alerts from Prometheus Alertmanager"

    async def run(self, **kwargs: Any) -> ToolResult:
        try:
            alerts = await fetch_firing_alerts()
            return ToolResult.ok(alerts)
        except Exception as e:
            return ToolResult.fail(str(e))


async def fetch_firing_alerts() -> list[dict[str, Any]]:
    """Returns all currently firing alerts from the Prometheus /api/v1/alerts endpoint."""
    base_url = get_settings().prometheus_url.rstrip("/")
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{base_url}/api/v1/alerts",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()

    alerts = payload.get("data", {}).get("alerts", [])
    return [a for a in alerts if a.get("state") == "firing"]


async def fetch_alert_rules() -> list[dict[str, Any]]:
    """Returns all configured alert rules (groups)."""
    base_url = get_settings().prometheus_url.rstrip("/")
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{base_url}/api/v1/rules",
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            payload = await resp.json()

    groups = payload.get("data", {}).get("groups", [])
    rules = []
    for group in groups:
        for rule in group.get("rules", []):
            if rule.get("type") == "alerting":
                rules.append(rule)
    return rules

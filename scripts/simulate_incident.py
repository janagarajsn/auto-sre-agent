"""
Simulate an incident by triggering the SRE agent with a synthetic alert.

Usage:
  python scripts/simulate_incident.py --alert PodCrashLooping --namespace default
  python scripts/simulate_incident.py --alert HighCpuUsage --severity medium
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from memory.schemas import AlertSignal, Severity
from agent.core.agent import run_incident
from tools.base import register_all_tools
from observability.logging import configure_logging


async def main() -> None:
    configure_logging()
    register_all_tools()

    parser = argparse.ArgumentParser(description="Simulate an SRE incident")
    parser.add_argument("--alert", default="PodCrashLooping")
    parser.add_argument("--namespace", default="default")
    parser.add_argument("--severity", default="high", choices=[s.value for s in Severity])
    parser.add_argument("--pod", default="crash-app")
    args = parser.parse_args()

    alert = AlertSignal(
        alert_name=args.alert,
        severity=Severity(args.severity),
        namespace=args.namespace,
        labels={
            "pod": args.pod,
            "container": "app",
            "env": "simulate",
        },
        annotations={"summary": f"Simulated {args.alert} alert"},
    )

    print(f"Triggering incident: {alert.alert_name} in {alert.namespace}")
    incident = await run_incident(alert)

    print(f"\nIncident completed:")
    print(f"  ID:     {incident.id}")
    print(f"  Status: {incident.status}")
    if incident.diagnosis:
        print(f"  Root cause: {incident.diagnosis.root_cause[:120]}")
    if incident.proposed_action:
        print(f"  Action: {incident.proposed_action.action_type} on {incident.proposed_action.target_resource}")
    if incident.action_result:
        print(f"  Result: {'success' if incident.action_result.success else 'failed'}")


if __name__ == "__main__":
    asyncio.run(main())

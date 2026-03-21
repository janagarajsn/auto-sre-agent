"""
Push synthetic metrics to Prometheus via the Pushgateway for local testing.

Usage:
  python scripts/seed_prometheus.py --scenario crashloop
  python scripts/seed_prometheus.py --scenario high-cpu
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from prometheus_client import CollectorRegistry, Counter, Gauge, push_to_gateway


def seed_crashloop(gateway: str) -> None:
    registry = CollectorRegistry()
    restarts = Counter(
        "kube_pod_container_status_restarts_total",
        "Simulated restart count",
        ["namespace", "pod", "container"],
        registry=registry,
    )
    restarts.labels(namespace="default", pod="simulate-app-abc123", container="app").inc(12)
    push_to_gateway(gateway, job="sre-agent-seed", registry=registry)
    print(f"Pushed crashloop metrics to {gateway}")


def seed_high_cpu(gateway: str) -> None:
    registry = CollectorRegistry()
    cpu = Gauge(
        "container_cpu_usage_ratio",
        "Simulated CPU ratio",
        ["namespace", "pod", "container"],
        registry=registry,
    )
    cpu.labels(namespace="default", pod="simulate-app-abc123", container="app").set(0.95)
    push_to_gateway(gateway, job="sre-agent-seed", registry=registry)
    print(f"Pushed high-cpu metrics to {gateway}")


SCENARIOS = {
    "crashloop": seed_crashloop,
    "high-cpu": seed_high_cpu,
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", choices=list(SCENARIOS), required=True)
    parser.add_argument("--gateway", default="http://localhost:9091")
    args = parser.parse_args()

    SCENARIOS[args.scenario](args.gateway)


if __name__ == "__main__":
    main()

"""
Alertmanager webhook payload schemas.
Mirrors the Alertmanager v2 API format.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AlertmanagerAlert(BaseModel):
    status: str  # "firing" | "resolved"
    labels: dict[str, str] = {}
    annotations: dict[str, str] = {}
    startsAt: datetime | None = None
    endsAt: datetime | None = None
    generatorURL: str | None = None
    fingerprint: str = ""


class AlertmanagerPayload(BaseModel):
    version: str = "4"
    groupKey: str = ""
    status: str = "firing"
    receiver: str = ""
    groupLabels: dict[str, str] = {}
    commonLabels: dict[str, str] = {}
    commonAnnotations: dict[str, str] = {}
    externalURL: str = ""
    alerts: list[AlertmanagerAlert] = []

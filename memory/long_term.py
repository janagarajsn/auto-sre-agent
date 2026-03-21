"""
Redis-backed incident memory store.

Responsibilities:
- Persist Incident records across agent restarts
- Query incident history for context injection
- TTL-based expiry aligned with settings.redis_ttl_seconds
"""

from __future__ import annotations

import json
from datetime import datetime
from uuid import UUID

import redis.asyncio as aioredis

from configs.settings import get_settings
from memory.schemas import Incident, IncidentStatus

_KEY_PREFIX = "sre:incident"


def _incident_key(incident_id: UUID) -> str:
    return f"{_KEY_PREFIX}:{incident_id}"


def _index_key() -> str:
    return f"{_KEY_PREFIX}:index"


class IncidentStore:
    def __init__(self, client: aioredis.Redis) -> None:
        self._r = client
        self._ttl = get_settings().redis_ttl_seconds

    async def save(self, incident: Incident) -> None:
        incident.updated_at = datetime.utcnow()
        key = _incident_key(incident.id)
        await self._r.set(key, incident.model_dump_json(), ex=self._ttl)
        # Maintain a sorted-set index by creation timestamp for range queries
        await self._r.zadd(
            _index_key(),
            {str(incident.id): incident.created_at.timestamp()},
        )

    async def get(self, incident_id: UUID) -> Incident | None:
        raw = await self._r.get(_incident_key(incident_id))
        if raw is None:
            return None
        return Incident.model_validate_json(raw)

    async def list_recent(self, limit: int = 20) -> list[Incident]:
        ids = await self._r.zrevrange(_index_key(), 0, limit - 1)
        incidents = []
        for id_bytes in ids:
            record = await self.get(UUID(id_bytes))
            if record:
                incidents.append(record)
        return incidents

    async def list_by_status(self, status: IncidentStatus) -> list[Incident]:
        all_incidents = await self.list_recent(limit=200)
        return [i for i in all_incidents if i.status == status]

    async def mark_resolved(self, incident_id: UUID) -> None:
        incident = await self.get(incident_id)
        if incident:
            incident.status = IncidentStatus.RESOLVED
            incident.resolved_at = datetime.utcnow()
            await self.save(incident)


async def get_incident_store() -> IncidentStore:
    settings = get_settings()
    client = aioredis.from_url(str(settings.redis_url), decode_responses=True)
    return IncidentStore(client)

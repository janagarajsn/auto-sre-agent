"""
Unit tests for the memory and state persistence layer.
"""

from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from memory.schemas import Incident, IncidentStatus, AlertSignal, Severity


class TestIncidentStore:
    @pytest.fixture
    def mock_redis_client(self):
        client = AsyncMock()
        client.set.return_value = True
        client.zadd.return_value = 1
        return client

    @pytest.mark.asyncio
    async def test_save_and_get(self, mock_redis_client, sample_incident):
        mock_redis_client.get.return_value = sample_incident.model_dump_json()

        with patch("memory.long_term.aioredis.from_url", return_value=mock_redis_client):
            from memory.long_term import IncidentStore
            store = IncidentStore(mock_redis_client)
            await store.save(sample_incident)
            retrieved = await store.get(sample_incident.id)

        assert retrieved is not None
        assert retrieved.id == sample_incident.id

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self, mock_redis_client):
        mock_redis_client.get.return_value = None

        from memory.long_term import IncidentStore
        store = IncidentStore(mock_redis_client)
        result = await store.get(uuid4())

        assert result is None

    @pytest.mark.asyncio
    async def test_mark_resolved_updates_status(self, mock_redis_client, sample_incident):
        sample_incident.status = IncidentStatus.EXECUTING
        mock_redis_client.get.return_value = sample_incident.model_dump_json()

        from memory.long_term import IncidentStore
        store = IncidentStore(mock_redis_client)
        await store.mark_resolved(sample_incident.id)

        # Verify set was called (the save after status update)
        assert mock_redis_client.set.called

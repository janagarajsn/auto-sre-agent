"""
Unit tests for tool integrations with mocked HTTP and Kubernetes clients.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestPrometheusClient:
    @pytest.mark.asyncio
    async def test_query_returns_results(self):
        mock_payload = {
            "status": "success",
            "data": {"result": [{"metric": {"pod": "app-1"}, "value": [1234567890, "45.2"]}]},
        }

        with patch("aiohttp.ClientSession") as MockSession:
            mock_resp = AsyncMock()
            mock_resp.json.return_value = mock_payload
            mock_resp.raise_for_status = MagicMock()
            MockSession.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_resp

            from tools.prometheus.client import PrometheusClient
            client = PrometheusClient(base_url="http://localhost:9090")
            result = await client.query("up")

        assert len(result) == 1
        assert result[0]["metric"]["pod"] == "app-1"

    @pytest.mark.asyncio
    async def test_query_raises_on_prometheus_error(self):
        mock_payload = {"status": "error", "error": "bad_data", "errorType": "bad_data"}

        with patch("aiohttp.ClientSession") as MockSession:
            mock_resp = AsyncMock()
            mock_resp.json.return_value = mock_payload
            mock_resp.raise_for_status = MagicMock()
            MockSession.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_resp

            from tools.prometheus.client import PrometheusClient
            client = PrometheusClient(base_url="http://localhost:9090")

            with pytest.raises(RuntimeError, match="Prometheus query failed"):
                await client.query("bad_query{")


class TestKubernetesRestartPod:
    @pytest.mark.asyncio
    async def test_restart_pod_success(self):
        with patch("tools.kubernetes.pods.get_core_v1") as mock_api_factory:
            mock_api = MagicMock()
            mock_api.delete_namespaced_pod.return_value = None
            mock_api_factory.return_value = mock_api

            from tools.kubernetes.pods import RestartPodTool
            tool = RestartPodTool()
            result = await tool.run(namespace="default", pod_name="my-app-abc123")

        assert result.success is True
        assert result.data["pod"] == "my-app-abc123"

    @pytest.mark.asyncio
    async def test_restart_pod_not_found(self):
        from kubernetes.client.rest import ApiException

        with patch("tools.kubernetes.pods.get_core_v1") as mock_api_factory:
            mock_api = MagicMock()
            mock_api.delete_namespaced_pod.side_effect = ApiException(status=404, reason="Not Found")
            mock_api_factory.return_value = mock_api

            from tools.kubernetes.pods import RestartPodTool
            tool = RestartPodTool()
            result = await tool.run(namespace="default", pod_name="missing-pod")

        assert result.success is False
        assert "404" in result.error


class TestRedisLocks:
    @pytest.mark.asyncio
    async def test_lock_acquired_and_released(self):
        mock_redis = AsyncMock()
        mock_redis.set.return_value = True
        mock_redis.get.return_value = "test-lock-value"
        mock_redis.delete.return_value = 1

        with patch("tools.redis.locks.get_redis_client", return_value=mock_redis):
            from tools.redis.locks import action_lock
            async with action_lock("test-resource") as acquired:
                assert acquired is True

    @pytest.mark.asyncio
    async def test_lock_not_acquired_when_already_locked(self):
        mock_redis = AsyncMock()
        mock_redis.set.return_value = None  # NX failed

        with patch("tools.redis.locks.get_redis_client", return_value=mock_redis):
            from tools.redis.locks import action_lock
            async with action_lock("test-resource") as acquired:
                assert acquired is False

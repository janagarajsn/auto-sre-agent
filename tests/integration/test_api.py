"""
Integration tests for the FastAPI layer using TestClient.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch, MagicMock

from memory.schemas import AlertSignal, Severity


@pytest.fixture
def client():
    with (
        patch("tools.redis.client.get_redis_client"),
        patch("tools.base.register_all_tools"),
        patch("observability.tracing.configure_tracing"),
    ):
        from api.main import create_app
        app = create_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture
def auth_headers():
    return {"X-API-Key": "change-me"}


class TestHealthEndpoints:
    def test_liveness_returns_200(self, client):
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_readiness_returns_200_when_redis_ok(self, client):
        with patch("api.routes.health.get_redis_client") as mock_factory:
            mock_redis = AsyncMock()
            mock_redis.ping.return_value = True
            mock_factory.return_value = mock_redis
            response = client.get("/readyz")
        assert response.status_code == 200


class TestAlertEndpoints:
    def test_webhook_requires_api_key(self, client):
        response = client.post("/alerts/", json={"alerts": []})
        assert response.status_code == 401

    def test_webhook_accepts_firing_alert(self, client, auth_headers):
        payload = {
            "version": "4",
            "status": "firing",
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "PodCrashLooping",
                        "severity": "high",
                        "namespace": "default",
                        "pod": "my-app-abc",
                    },
                    "annotations": {"summary": "Pod is crash looping"},
                }
            ],
        }

        with patch("api.routes.alerts.run_incident", new_callable=AsyncMock):
            response = client.post("/alerts/", json=payload, headers=auth_headers)

        assert response.status_code == 202
        assert response.json()["dispatched"] == 1

    def test_webhook_skips_resolved_alerts(self, client, auth_headers):
        payload = {
            "alerts": [{"status": "resolved", "labels": {"alertname": "Foo"}, "annotations": {}}]
        }
        response = client.post("/alerts/", json=payload, headers=auth_headers)
        assert response.status_code == 202
        assert response.json()["dispatched"] == 0


class TestIncidentEndpoints:
    def test_list_incidents_requires_auth(self, client):
        response = client.get("/incidents/")
        assert response.status_code == 401

    def test_list_incidents_returns_empty_list(self, client, auth_headers):
        with patch("api.routes.incidents.get_incident_store") as mock_store_factory:
            mock_store = AsyncMock()
            mock_store.list_recent.return_value = []
            mock_store_factory.return_value = mock_store
            response = client.get("/incidents/", headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == []

"""Tests for health endpoints."""

import pytest
from fastapi.testclient import TestClient
from condense.server.app import create_app
from condense.config.loader import reset_config_cache


@pytest.fixture
def client(tmp_path):
    reset_config_cache()
    config_file = tmp_path / "condense.yaml"
    config_file.write_text("""
upstream:
  url: "https://api.openai.com/v1"
deployment:
  port: 8080
""")
    app = create_app(str(config_file))
    with TestClient(app) as c:
        yield c


class TestHealthEndpoints:
    def test_health(self, client):
        """Health endpoint returns healthy."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["service"] == "condense"

    def test_health_ready(self, client):
        """Readiness endpoint returns ready when config is loaded."""
        response = client.get("/health/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ready"
        assert data["config_loaded"] is True

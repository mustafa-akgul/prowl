"""Tests for the FastAPI dashboard endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_endpoint_returns_ok():
    """The /health liveness probe answers 200 with a status payload.

    This is the endpoint the docker-compose healthcheck targets, so it must
    work without any API credentials configured.
    """
    from agent.dashboard import app

    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

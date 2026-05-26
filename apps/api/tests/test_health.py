"""Smoke tests for the health endpoints (Phase 1)."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_200(app_client: TestClient) -> None:
    response = app_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "agentforge-api"
    assert body["llm_provider"] == "deepseek"
    assert body["llm_model"] == "deepseek-v4-flash"


def test_ready_returns_200_when_db_reachable(app_client: TestClient) -> None:
    response = app_client.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"]["db"] == "ok"
    assert body["checks"]["llm_provider"] == "DeepSeek API"
    assert body["checks"]["deepseek_api_key"] == "configured"


def test_openapi_includes_all_phase_1_routes(app_client: TestClient) -> None:
    """Verify the OpenAPI schema exposes the full contract surface from Phase 1."""
    response = app_client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]

    # Health
    assert "/health" in paths
    assert "/health/ready" in paths
    assert "/metrics" in paths

    # Sessions
    assert "/sessions" in paths
    assert "/sessions/{session_id}" in paths
    assert "/sessions/{session_id}/answer" in paths
    assert "/sessions/{session_id}/finalise" in paths
    assert "/sessions/{session_id}/events" in paths

    # Files
    assert "/sessions/{session_id}/files" in paths
    assert "/sessions/{session_id}/artifacts/{relative_path}" in paths

    # Approvals
    assert "/sessions/{session_id}/approve" in paths
    assert "/sessions/{session_id}/reject" in paths

    # Audit
    assert "/audit/export/{session_id}" in paths

    # Evals
    assert "/evals/latest" in paths
    assert "/evals/run" in paths

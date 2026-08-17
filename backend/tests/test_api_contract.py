from fastapi.testclient import TestClient

from app.main import app


def test_openapi_contract_exposes_core_paths() -> None:
    schema = app.openapi()
    paths = schema["paths"]

    assert "/health" in paths
    assert "/auth/login" in paths
    assert "/api/ai-chat" in paths
    assert "/reconciliation/chart-of-accounts" in paths
    assert "/reconciliation/auto-match-selected" in paths
    assert "/reconciliation/multi-manual-match" in paths
    assert "/reconciliation/ai-match" in paths
    assert "/reconciliation/groups" in paths
    assert "/reconciliation/session" in paths
    assert "/reconciliation/reset" in paths
    assert "/reconciliation/gl/ensure-draft" in paths
    assert "/reconciliation/gl/by-group/{group_id}" in paths


def test_health_endpoint_smoke() -> None:
    client = TestClient(app)
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"

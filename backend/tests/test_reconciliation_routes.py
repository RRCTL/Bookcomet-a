"""Focused tests for restored reconciliation match/GL HTTP routes."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    return TestClient(app)


def test_groups_endpoint_requires_auth(client: TestClient) -> None:
    response = client.get("/reconciliation/groups")
    assert response.status_code in (401, 403)


def test_ai_match_mocked(client: TestClient) -> None:
    mock_result = {
        "duplicates": [],
        "matches": [
            {
                "bank_txn_id": "b1",
                "ledger_txn_id": "l1",
                "score": 0.95,
                "match_type": "1:1",
                "ai_reason": "amount and date align",
            }
        ],
        "summary": "1 match found",
    }
    with patch("app.api.reconciliation_match_gl.run_ai_match", return_value=(mock_result, {})):
        response = client.post(
            "/reconciliation/ai-match",
            json={"bank_txn_ids": ["b1"], "ledger_txn_ids": ["l1"]},
        )
    # Unauthenticated requests are rejected before handler runs
    assert response.status_code in (401, 403)

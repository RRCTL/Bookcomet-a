"""Manager Review sends slim LLM payloads (no heavy OCR blobs)."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.handlers import (
    _slim_manager_input_payload,
    _trim_manager_llm_row,
    manager_review_node,
)


def test_trim_manager_llm_row_keeps_page():
    row = {
        "amount": "100",
        "payee": "Vendor",
        "_page": 3,
        "source_file": "receipts.pdf P1",
        "raw_ocr_text": "noise",
    }
    slim = _trim_manager_llm_row(row)
    assert slim["_page"] == 3
    assert slim["source_file"] == "receipts.pdf P1"
    assert "raw_ocr_text" not in slim


def test_trim_manager_llm_row_drops_heavy_fields():
    row = {
        "amount": "100",
        "date": "2025-01-01",
        "payee": "Vendor",
        "raw_ocr_text": "x" * 5000,
        "extraction_provenance": {"vlm": "blob"},
    }
    slim = _trim_manager_llm_row(row)
    assert slim == {"amount": "100", "date": "2025-01-01", "payee": "Vendor"}
    assert "raw_ocr_text" not in slim


def test_slim_manager_input_payload_removes_duplicate_group_rows():
    payload = {
        "processing_mode": "AP",
        "selected_rows": [{"amount": "1", "raw_ocr_text": "noise"}],
        "selected_group": {"group_id": "g1", "rows": [{"amount": "1"}], "count": 2},
    }
    slim = _slim_manager_input_payload(payload, tie_break=False)
    assert slim["selected_rows"] == [{"amount": "1"}]
    assert slim["selected_group"] == {"group_id": "g1", "count": 2}


@pytest.mark.asyncio
async def test_manager_review_passes_slim_payload_to_vlm():
    run = SimpleNamespace(id="run-1", company_id="co-1", processing_mode="AP", graph_json={}, node_states_json={})
    manager_node = {
        "id": "manager",
        "type": "ManagerReview",
        "data": {"provider": "Qwen"},
    }
    vote_payload = {
        "selected_rows": [{"amount": "100", "raw_ocr_text": "heavy"}],
        "selected_group": {"rows": [{"amount": "100"}], "count": 1},
        "reason": "selected",
    }

    with patch("app.graph.nodes.handlers._incoming_payloads", return_value=[vote_payload]):
        with patch(
            "app.graph.nodes.handlers._call_workflow_vlm",
            new_callable=AsyncMock,
            return_value={
                "provider": "Qwen",
                "model": "test-model",
                "data": {"status": "pass", "reason": "ok"},
                "raw": "{}",
            },
        ) as call_vlm:
            with patch("app.graph.nodes.handlers.record_node_execution"):
                await manager_review_node(MagicMock(), run, manager_node, None)

    sent = call_vlm.await_args.kwargs["input_payload"]
    assert sent["selected_rows"] == [{"amount": "100"}]
    assert "rows" not in sent.get("selected_group", {})

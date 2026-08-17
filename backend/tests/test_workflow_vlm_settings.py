from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import workflow_provider_options
from app.graph.default_graphs import build_default_graph
from app.graph.graph_utils import ap_vlm_model_override_for_provider
from app.graph.workflow_service import WorkflowService


def test_ap_vlm_model_override_for_provider_always_none():
    assert ap_vlm_model_override_for_provider("DeepSeek") is None
    assert ap_vlm_model_override_for_provider("Qwen") is None


def test_workflow_provider_options_default():
    assert workflow_provider_options()
    assert "Qwen" in workflow_provider_options()


def test_workflow_provider_options_from_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WORKFLOW_PROVIDERS", "Qwen,Enhance")
    from app.core.config import workflow_provider_options

    assert workflow_provider_options() == ["Qwen", "Enhance"]


def test_vlm_ocr_kwargs_ap_cross_with_explicit_model():
    graph = build_default_graph("AP")
    for node in graph["nodes"]:
        if node.get("id") == "vlm":
            node["data"]["crossVlm"] = True
            node["data"]["provider"] = "Qwen"
            node["data"]["model"] = "custom-vlm-model"
    run = SimpleNamespace(graph_json=graph, processing_mode="AP")
    kwargs = WorkflowService._vlm_ocr_kwargs(run)
    assert kwargs["ap_force_cross_verify"] is True
    assert kwargs["ap_vlm_model_override"] == "custom-vlm-model"


def test_vlm_ocr_kwargs_ap_cross_no_override_without_explicit_model():
    graph = build_default_graph("AP")
    for node in graph["nodes"]:
        if node.get("id") == "vlm":
            node["data"]["crossVlm"] = True
            node["data"]["provider"] = "DeepSeek"
    run = SimpleNamespace(graph_json=graph, processing_mode="AP")
    kwargs = WorkflowService._vlm_ocr_kwargs(run)
    assert kwargs["ap_force_cross_verify"] is True
    assert "ap_vlm_model_override" not in kwargs


def test_vlm_ocr_kwargs_bank_ignores_cross():
    graph = build_default_graph("BANK")
    for node in graph["nodes"]:
        if node.get("id") == "vlm":
            node["data"]["crossVlm"] = True
    run = SimpleNamespace(graph_json=graph, processing_mode="BANK")
    kwargs = WorkflowService._vlm_ocr_kwargs(run)
    assert kwargs["ap_force_cross_verify"] is False
    assert "ap_vlm_model_override" not in kwargs


@pytest.mark.asyncio
async def test_process_one_file_passes_vlm_graph_settings():
    graph = build_default_graph("AP")
    explicit_model = "custom-vlm-model"
    for node in graph["nodes"]:
        if node.get("id") == "vlm":
            node["data"]["crossVlm"] = True
            node["data"]["provider"] = "Qwen"
            node["data"]["model"] = explicit_model

    run = SimpleNamespace(
        id="run-1",
        company_id="co-1",
        processing_mode="AP",
        graph_json=graph,
    )
    run_file = SimpleNamespace(
        file_status="pending",
        error_text=None,
        gate_result=None,
        result_summary_json=None,
    )
    task_file = SimpleNamespace(
        storage_path=__file__,
        original_filename="test.pdf",
    )
    db = MagicMock()
    db.commit = MagicMock()

    with patch("app.graph.workflow_service.ocr_test_core", new_callable=AsyncMock) as mock_ocr:
        mock_ocr.return_value = {"tsv_rows": []}
        with patch("app.services.abuse_guard.company_ocr_concurrency"):
            with patch("app.core.db_concurrency.long_running_db_work_slot"):
                await WorkflowService._process_one_file(
                    db,
                    run,
                    run_file,
                    task_file,
                    ap_vlm_model_override=explicit_model,
                    ap_force_cross_verify=True,
                )

    mock_ocr.assert_awaited_once()
    call_kwargs = mock_ocr.call_args.kwargs
    assert call_kwargs["ap_force_cross_verify"] is True
    assert call_kwargs["ap_vlm_model_override"] == explicit_model

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.graph.default_graphs import build_default_graph
from app.graph.graph_utils import double_check_node_params, vlm_node_params, vlm_settings


def test_vlm_node_params_from_graph():
    g = build_default_graph("AP")
    params = vlm_node_params(g)
    assert params["provider"] == "Qwen"
    assert params["crossVlm"] is False


def test_double_check_params_when_node_present():
    legacy = {
        "nodes": build_default_graph("AP")["nodes"],
        "edges": build_default_graph("AP")["edges"],
        "processingMode": "AP",
    }
    for n in legacy["nodes"]:
        if n.get("type") == "VLM_API":
            n["data"]["crossVlm"] = True
    from app.graph.graph_schema_v2 import normalize_graph_v2

    g2 = normalize_graph_v2(legacy, "AP")
    dc = double_check_node_params(g2)
    assert dc is not None
    from app.core.config import default_workflow_provider

    assert dc["provider"] == default_workflow_provider()
    assert dc["enabled"] is True


def test_vlm_settings_detects_double_check_node():
    g = build_default_graph("AP")
    from app.graph.graph_schema_v2 import normalize_graph_v2

    g["nodes"][2]["data"]["crossVlm"] = True if g["nodes"][2].get("type") != "VLM_API" else None
    for n in g["nodes"]:
        if n.get("type") == "VLM_API":
            n["data"]["crossVlm"] = True
    g2 = normalize_graph_v2(g, "AP")
    provider, cross = vlm_settings(g2)
    assert provider == "Qwen"
    assert cross is True


@pytest.mark.asyncio
async def test_save_approved_snapshot_writes_pool2(tmp_path):
    from app.graph.workflow_service import WorkflowService
    from app.models.workflow import WorkflowPool2Package
    from app.services.pool2_storage import Pool2Storage

    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        company_id="co-1",
        processing_mode="AP",
        graph_json=build_default_graph("AP"),
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
        snapshot_message_id=None,
    )
    task = SimpleNamespace(id="task-1", has_spreadsheet=False, status="idle", updated_at=None)
    db = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()
    db.add = MagicMock()

    query_mock = MagicMock()
    query_mock.filter.return_value.first.return_value = task
    query_mock.filter.return_value.delete.return_value = None
    query_mock.filter.return_value.order_by.return_value.first.return_value = None
    db.query.return_value = query_mock

    payload = {"arapTransactions": [{"id_number": "42"}]}

    with patch("app.graph.workflow_service.pool2", Pool2Storage(root=tmp_path)):
        with patch("app.graph.node_runtime.pool2", Pool2Storage(root=tmp_path)):
            await WorkflowService.save_approved_snapshot(db, run, payload, node={"id": "save"})

    assert run.run_status == "completed"
    assert run.node_states_json.get("pool2_package_id")
    db.add.assert_called()
    added_types = [type(call.args[0]).__name__ for call in db.add.call_args_list if call.args]
    assert "WorkflowPool2Package" in added_types or WorkflowPool2Package.__name__ in str(added_types)

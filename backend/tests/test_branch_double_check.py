from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.default_graphs import build_default_graph
from app.graph.graph_utils import graph_nodes
from app.graph.workflow_service import WorkflowService


def _graph_with_if_double_check():
    graph = build_default_graph("AP")
    graph["nodes"].extend(
        [
            {
                "id": "if_node",
                "type": "If",
                "position": {"x": 0, "y": 0},
                "data": {"label": "If", "condition": "needs_double_check"},
            },
            {
                "id": "vlm_double_check",
                "type": "VLMDoubleCheck",
                "position": {"x": 0, "y": 0},
                "data": {"label": "Double Check", "provider": "DeepSeek", "enabled": True},
            },
        ]
    )
    graph["edges"] = [
        edge
        for edge in graph["edges"]
        if not (edge.get("source") == "vlm" and edge.get("target") == "table")
    ]
    graph["edges"].extend(
        [
            {"id": "vlm-if", "source": "vlm", "target": "if_node", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "if-dc", "source": "if_node", "target": "vlm_double_check", "sourceHandle": "true", "targetHandle": "in"},
            {"id": "dc-table", "source": "vlm_double_check", "target": "table", "sourceHandle": "out", "targetHandle": "in"},
        ]
    )
    return graph


@pytest.mark.asyncio
async def test_double_check_only_processes_branch_warning_files():
    graph = _graph_with_if_double_check()
    dc_node = next(n for n in graph_nodes(graph) if n.get("type") == "VLMDoubleCheck")
    run = SimpleNamespace(
        id="run-1",
        company_id="co-1",
        task_id="task-1",
        processing_mode="AP",
        graph_json=graph,
        run_status="draft",
        node_states_json={
            "file_routes": {
                "if_node": {
                    "true": ["warn-file"],
                    "false": ["ok-file"],
                }
            }
        },
        console_log_json=[],
    )
    warn_rf = SimpleNamespace(
        task_file_id="warn-file",
        file_status="warning",
        batch_receipt_signal=None,
        batch_table_preset=None,
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"total": "1"}]}},
    )
    ok_rf = SimpleNamespace(
        task_file_id="ok-file",
        file_status="ok",
        batch_receipt_signal=None,
        batch_table_preset=None,
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"total": "2"}]}},
    )
    task_file = SimpleNamespace(id="warn-file")
    db = MagicMock()

    def _query(model):
        chain = MagicMock()
        if getattr(model, "__name__", "") == "TaskFile":
            chain.filter.return_value.all.return_value = [task_file]
        else:
            chain.filter.return_value.all.return_value = [warn_rf, ok_rf]
        return chain

    db.query.side_effect = _query
    processed: list[str] = []

    async def _track_process(_db, _run, rf, _tf, **kwargs):
        processed.append(rf.task_file_id)
        return {"ok": True}

    with patch.object(WorkflowService, "_process_one_file", new=AsyncMock(side_effect=_track_process)):
        with patch("app.graph.node_runtime.record_node_execution"):
            with patch("app.graph.workflow_events.workflow_event_hub") as hub:
                hub.snapshot = AsyncMock()
                await WorkflowService.execute_double_check_stage(db, run, node=dc_node)

    assert processed == ["warn-file"]

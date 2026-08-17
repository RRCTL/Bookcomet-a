from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.default_graphs import build_default_graph
from app.graph.nodes.handlers import coa_node, save_node
from app.graph.workflow_service import WorkflowService, _prepare_re_vlm_node_states


@pytest.mark.asyncio
async def test_coa_node_calls_deploy_with_skip_flag():
    run = SimpleNamespace(
        node_states_json={
            "approved_payload": {"arapTransactions": []},
            "skip_coa": True,
        }
    )
    db = MagicMock()

    with patch.object(
        WorkflowService, "deploy_approved_coa", new_callable=AsyncMock
    ) as mock_deploy:
        await coa_node(db, run, {}, None)
        mock_deploy.assert_awaited_once_with(
            db, run, {"arapTransactions": []}, skip_coa=True
        )


@pytest.mark.asyncio
async def test_save_node_calls_save_snapshot():
    payload = {"arapTransactions": [{"id_number": "1"}]}
    run = SimpleNamespace(node_states_json={"approved_payload": payload})
    db = MagicMock()

    with patch.object(
        WorkflowService, "save_approved_snapshot", new_callable=AsyncMock
    ) as mock_save:
        await save_node(db, run, {}, None)
        mock_save.assert_awaited_once_with(db, run, payload, node={})


@pytest.mark.asyncio
async def test_resume_run_stashes_payload_and_resumes_graph_from_save():
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        company_id="co-1",
        processing_mode="AP",
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
    )
    task = SimpleNamespace(id="task-1")
    db = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()

    query_mock = MagicMock()
    query_mock.filter.return_value.first.return_value = task
    db.query.return_value = query_mock

    payload = {"arapTransactions": []}

    with patch(
        "app.graph.executor.run_workflow_graph", new_callable=AsyncMock
    ) as mock_graph:
        result = await WorkflowService.resume_run(
            db,
            run,
            approved_payload=payload,
            skip_coa=False,
            user_id="user-1",
        )

    assert run.node_states_json["approved_payload"] == payload
    assert run.node_states_json["skip_coa"] is False
    mock_graph.assert_awaited_once()
    assert mock_graph.call_args.kwargs["start_at"] == "save"
    assert result is run


@pytest.mark.asyncio
async def test_deploy_approved_coa_skip_sets_skipped_status():
    run = SimpleNamespace(
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
        company_id="co-1",
        processing_mode="AP",
    )
    db = MagicMock()
    db.commit = MagicMock()

    await WorkflowService.deploy_approved_coa(
        db, run, {"arapTransactions": []}, skip_coa=True
    )

    assert run.node_states_json["coa"]["status"] == "skipped"


@pytest.mark.asyncio
async def test_re_vlm_finalize_message_includes_row_count():
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        company_id="co-1",
        processing_mode="AP",
        graph_json=build_default_graph("AP"),
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
    )
    run_file = SimpleNamespace(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="ok",
        result_summary_json={"tsv_rows": [{"voucher_no": "V1"}]},
    )
    task_file = SimpleNamespace(id="tf-1", storage_path=__file__, original_filename="x.pdf")

    db = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()

    class _Chain:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_a, **_k):
            return self

        def all(self):
            return self._rows

        def first(self):
            return self._rows[0] if self._rows else None

    def query_side_effect(model):
        name = getattr(model, "__name__", str(model))
        if name == "WorkflowRunFile":
            return _Chain([run_file])
        if name == "TaskFile":
            return _Chain([task_file])
        return _Chain([])

    db.query.side_effect = query_side_effect

    captured: dict = {}

    def capture_finalize(r, files, *, ok_count, warn_count, console_message, **kwargs):
        captured["message"] = console_message

    with patch.object(
        WorkflowService, "_process_one_file", new_callable=AsyncMock, return_value={"ok": True}
    ):
        with patch(
            "app.graph.workflow_service._finalize_run_after_vlm", side_effect=capture_finalize
        ):
            await WorkflowService.re_vlm_files(db, run, ["tf-1"])

    assert "1 row(s) in table" in captured["message"]


@pytest.mark.asyncio
async def test_re_vlm_passes_rescan_hints_to_process_one_file():
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        company_id="co-1",
        processing_mode="AP",
        graph_json=build_default_graph("AP"),
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
    )
    run_file = SimpleNamespace(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="warning",
        gate_result="NON_TRANSACTIONAL",
        error_text="needs_confirmation",
        result_summary_json={"tsv_rows": []},
        batch_table_preset=None,
        batch_receipt_signal=None,
    )
    task_file = SimpleNamespace(id="tf-1", storage_path=__file__, original_filename="x.pdf")

    db = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()

    class _Chain:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_a, **_k):
            return self

        def all(self):
            return self._rows

        def first(self):
            return self._rows[0] if self._rows else None

    def query_side_effect(model):
        name = getattr(model, "__name__", str(model))
        if name == "WorkflowRunFile":
            return _Chain([run_file])
        if name == "TaskFile":
            return _Chain([task_file])
        return _Chain([])

    db.query.side_effect = query_side_effect

    with patch.object(
        WorkflowService, "_process_one_file", new_callable=AsyncMock, return_value={"ok": True}
    ) as mock_process:
        with patch("app.graph.workflow_service._finalize_run_after_vlm"):
            with patch(
                "app.graph.workflow_service._stream_incremental_table", new_callable=AsyncMock
            ):
                with patch(
                    "app.graph.workflow_events.workflow_event_hub.snapshot",
                    new_callable=AsyncMock,
                ):
                    await WorkflowService.re_vlm_files(
                        db,
                        run,
                        ["tf-1"],
                        rescan_reasons=["wrong_amount", "missed_receipts"],
                        rescan_note="Three taxi receipts",
                    )

    mock_process.assert_awaited_once()
    kwargs = mock_process.call_args.kwargs
    assert kwargs["rescan_reasons"] == ["wrong_amount", "missed_receipts"]
    assert kwargs["rescan_note"] == "Three taxi receipts"
    assert any("Re-VLM reasons" in line.get("message", "") for line in run.console_log_json)


def test_prepare_re_vlm_node_states_resets_stale_finished_nodes():
    run = SimpleNamespace(
        graph_json=build_default_graph("AP"),
        node_states_json={
            "files": {"status": "completed"},
            "mode": {"status": "completed"},
            "receipt": {"status": "completed"},
            "vlm": {"status": "completed"},
            "table": {"status": "active", "detail": {"row_count": 3}},
            "save": {"status": "completed"},
            "merged_ocr": [{"amount": "1"}],
            "ocr_by_file": {"tf-1": [{"amount": "1"}]},
        },
    )

    vlm_id, table_id = _prepare_re_vlm_node_states(run, file_count=2)

    assert vlm_id == "vlm"
    assert table_id == "table"
    assert run.node_states_json["files"]["status"] == "pending"
    assert run.node_states_json["receipt"]["status"] == "pending"
    assert run.node_states_json["table"]["status"] == "pending"
    assert run.node_states_json["vlm"]["status"] == "running"
    assert run.node_states_json["vlm"]["detail"] == {"file_count": 2}
    assert run.node_states_json["merged_ocr"] == [{"amount": "1"}]
    assert run.node_states_json["ocr_by_file"] == {"tf-1": [{"amount": "1"}]}


def test_prepare_re_vlm_node_states_marks_vote_proposers_running():
    graph = build_default_graph("AP")
    graph["nodes"] = [n for n in graph["nodes"] if n.get("type") != "VLM_API"]
    graph["edges"] = [e for e in graph["edges"] if e.get("source") != "vlm" and e.get("target") != "vlm"]
    graph["nodes"].extend(
        [
            {
                "id": "proposal_a",
                "type": "VLMProposer",
                "position": {"x": 0, "y": 0},
                "data": {"nodeType": "VLMProposer"},
            },
            {
                "id": "proposal_b",
                "type": "VLMProposer",
                "position": {"x": 0, "y": 0},
                "data": {"nodeType": "VLMProposer"},
            },
            {
                "id": "merge",
                "type": "MergeResult",
                "position": {"x": 0, "y": 0},
                "data": {"nodeType": "MergeResult"},
            },
        ]
    )
    graph["edges"].extend(
        [
            {"id": "r-a", "source": "receipt", "target": "proposal_a"},
            {"id": "r-b", "source": "receipt", "target": "proposal_b"},
            {"id": "a-m", "source": "proposal_a", "target": "merge"},
            {"id": "b-m", "source": "proposal_b", "target": "merge"},
            {"id": "m-t", "source": "merge", "target": "table"},
        ]
    )
    run = SimpleNamespace(
        graph_json=graph,
        node_states_json={
            "proposal_a": {"status": "completed"},
            "proposal_b": {"status": "completed"},
            "merge": {"status": "completed"},
            "table": {"status": "active"},
        },
    )

    vlm_id, _table_id = _prepare_re_vlm_node_states(run, file_count=1)

    assert vlm_id == "proposal_a"
    assert run.node_states_json["proposal_a"]["status"] == "running"
    assert run.node_states_json["proposal_b"]["status"] == "running"
    assert run.node_states_json["merge"]["status"] == "pending"
    assert run.node_states_json["table"]["status"] == "pending"


def test_prepare_re_vlm_node_states_includes_rescan_focus():
    run = SimpleNamespace(
        graph_json=build_default_graph("AP"),
        node_states_json={},
    )

    _prepare_re_vlm_node_states(
        run,
        file_count=1,
        rescan_reasons=["wrong_amount", "wrong_date"],
        rescan_note="use JPY total",
    )

    detail = run.node_states_json["vlm"]["detail"]
    assert detail["file_count"] == 1
    assert detail["rescan_focus"] == "Wrong amount, Wrong date"
    assert detail["rescan_note"] == "use JPY total"
    assert detail["reason"] == "Re-VLM: Wrong amount, Wrong date"

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.workflow_service import (
    WorkflowService,
    _CANCEL_REQUESTED_KEY,
    workflow_run_cancel_requested,
    workflow_run_should_abort_processing,
)
from app.models.workflow import WorkflowRun, WorkflowRunFile


@pytest.mark.asyncio
async def test_request_run_cancel_hard_stops_executing_run():
    run = SimpleNamespace(
        id="run-1",
        run_status="executing",
        node_states_json={"vlm": {"status": "running"}},
        graph_json={"nodes": [{"id": "vlm", "type": "VLM_API"}]},
        console_log_json=[],
    )
    db = MagicMock()
    finish = AsyncMock(return_value={"cancelled": True})

    with patch("app.graph.workflow_service._finish_vlm_after_cancel", finish):
        result = await WorkflowService.request_run_cancel(db, run)

    assert result is run
    finish.assert_called_once()
    db.commit.assert_called()


def test_request_run_cancel_recovers_wedged_when_not_executing():
    run = SimpleNamespace(
        id="run-1",
        run_status="draft",
        node_states_json={"vlm": {"status": "running"}},
        console_log_json=[],
    )
    db = MagicMock()

    with patch.object(WorkflowService, "_recover_wedged_node_states") as recover:
        import asyncio

        updated = asyncio.run(WorkflowService.request_run_cancel(db, run))
        recover.assert_called_once_with(db, run)
        assert updated is run


def test_workflow_run_cancel_requested_reads_fresh_row():
    run = WorkflowRun(id="run-cancel-test", node_states_json={_CANCEL_REQUESTED_KEY: True})
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = run

    with patch("app.database.SessionLocal") as session_local:
        session_local.return_value = db
        assert workflow_run_cancel_requested("run-cancel-test") is True
        db.close.assert_called_once()


def test_workflow_run_should_abort_when_run_no_longer_executing():
    run = WorkflowRun(id="run-stop", run_status="draft", node_states_json={})
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = run

    with patch("app.database.SessionLocal") as session_local:
        session_local.return_value = db
        assert workflow_run_should_abort_processing("run-stop") is True


@pytest.mark.asyncio
async def test_finish_vlm_after_cancel_keeps_completed_and_leaves_pending():
    from app.graph.workflow_service import _finish_vlm_after_cancel

    rf1 = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="ok",
        result_summary_json={"tsv_rows": [{"total": "1"}]},
    )
    rf2 = WorkflowRunFile(
        id="rf-2",
        run_id="run-1",
        task_file_id="tf-2",
        file_status="pending",
    )
    run = SimpleNamespace(
        id="run-1",
        run_status="executing",
        node_states_json={"cancel_requested": True, "vlm": {"status": "running"}},
        graph_json={"nodes": [{"id": "vlm", "type": "VLM_API"}]},
        console_log_json=[],
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [rf1, rf2]
    hub = MagicMock()
    hub.snapshot = AsyncMock()

    summary = await _finish_vlm_after_cancel(
        db,
        run,
        node_id="vlm",
        finalize_table=True,
        event_hub=hub,
    )

    assert summary["cancelled"] is True
    assert summary["ok_count"] == 1
    assert rf1.file_status == "ok"
    assert rf2.file_status == "pending"
    assert run.run_status == "awaiting_review"
    assert run.node_states_json["vlm"]["status"] == "cancelled"
    assert "cancel_requested" not in run.node_states_json

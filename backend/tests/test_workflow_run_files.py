from types import SimpleNamespace
from unittest.mock import MagicMock
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.graph.workflow_service import WorkflowService, _promote_run_to_awaiting_review
from app.models.workflow import WorkflowRunFile


def _mock_db(*, task_files, existing_run_files):
    db = MagicMock()
    task_file_query = MagicMock()
    task_file_query.filter.return_value.all.return_value = task_files

    run_file_query = MagicMock()
    run_file_query.filter.return_value.all.return_value = existing_run_files

    db.query.side_effect = lambda model: (
        task_file_query if model.__name__ == "TaskFile" else run_file_query
    )
    return db


def test_sync_run_files_removes_orphaned_run_files():
    task_file = SimpleNamespace(id="tf-1")
    orphan = WorkflowRunFile(
        id="rf-orphan",
        run_id="run-1",
        task_file_id="tf-deleted",
        file_status="pending",
    )
    db = _mock_db(task_files=[task_file], existing_run_files=[orphan])
    run = SimpleNamespace(id="run-1", task_id="task-1")

    out = WorkflowService.sync_run_files(db, run)

    assert len(out) == 1
    assert out[0].task_file_id == "tf-1"
    db.delete.assert_called_once_with(orphan)


def test_prepare_resets_stuck_running_on_draft():
    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="running",
    )
    db = _mock_db(task_files=[SimpleNamespace(id="tf-1")], existing_run_files=[run_file])
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="draft", graph_json={}, node_states_json={})

    processable, all_files = WorkflowService.prepare_run_files_for_execute(db, run)

    assert len(processable) == 1
    assert processable[0].file_status == "pending"
    assert len(all_files) == 1


def test_prepare_resets_ok_files_on_draft_for_rerun():
    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="ok",
        batch_committed_at=None,
    )
    db = _mock_db(task_files=[SimpleNamespace(id="tf-1")], existing_run_files=[run_file])
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="draft", graph_json={}, node_states_json={})

    processable, _ = WorkflowService.prepare_run_files_for_execute(db, run)

    assert len(processable) == 1
    assert processable[0].file_status == "pending"


def test_prepare_draft_second_batch_does_not_reset_committed_ok():
    committed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    batch1 = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="ok",
        upload_batch_id="batch-1",
        batch_committed_at=committed,
    )
    batch2 = WorkflowRunFile(
        id="rf-2",
        run_id="run-1",
        task_file_id="tf-2",
        file_status="pending",
        upload_batch_id="batch-2",
        batch_committed_at=None,
    )
    db = _mock_db(
        task_files=[SimpleNamespace(id="tf-1"), SimpleNamespace(id="tf-2")],
        existing_run_files=[batch1, batch2],
    )
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="draft", graph_json={}, node_states_json={})

    processable, all_files = WorkflowService.prepare_run_files_for_execute(db, run)

    assert [rf.task_file_id for rf in processable] == ["tf-2"]
    assert batch1.file_status == "ok"
    assert batch2.file_status == "pending"
    assert batch2.batch_committed_at is not None
    assert len(all_files) == 2


def test_prepare_second_batch_skips_committed_failed():
    committed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    batch1_failed = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="failed",
        upload_batch_id="batch-1",
        batch_committed_at=committed,
        error_text="ocr error",
    )
    batch2 = WorkflowRunFile(
        id="rf-2",
        run_id="run-1",
        task_file_id="tf-2",
        file_status="pending",
        upload_batch_id="batch-2",
        batch_committed_at=None,
    )
    db = _mock_db(
        task_files=[SimpleNamespace(id="tf-1"), SimpleNamespace(id="tf-2")],
        existing_run_files=[batch1_failed, batch2],
    )
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="draft", graph_json={}, node_states_json={})

    processable, all_files = WorkflowService.prepare_run_files_for_execute(db, run)

    assert [rf.task_file_id for rf in processable] == ["tf-2"]
    assert batch1_failed.file_status == "failed"
    assert batch1_failed.error_text == "ocr error"
    assert batch2.batch_committed_at is not None
    assert len(all_files) == 2


def test_prepare_unwedges_all_committed_draft_run():
    # A prior crashed/interrupted execute left every file committed-but-not-ok.
    committed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    pending_committed = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="pending",
        upload_batch_id="batch-1",
        batch_committed_at=committed,
    )
    failed_committed = WorkflowRunFile(
        id="rf-2",
        run_id="run-1",
        task_file_id="tf-2",
        file_status="failed",
        upload_batch_id="batch-1",
        batch_committed_at=committed,
    )
    db = _mock_db(
        task_files=[SimpleNamespace(id="tf-1"), SimpleNamespace(id="tf-2")],
        existing_run_files=[pending_committed, failed_committed],
    )
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="draft", graph_json={}, node_states_json={})

    processable, all_files = WorkflowService.prepare_run_files_for_execute(db, run)

    assert {rf.task_file_id for rf in processable} == {"tf-1", "tf-2"}
    assert len(all_files) == 2


def test_prepare_raises_when_no_task_files():
    db = _mock_db(task_files=[], existing_run_files=[])
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="draft", graph_json={}, node_states_json={})

    with pytest.raises(HTTPException) as exc:
        WorkflowService.prepare_run_files_for_execute(db, run)

    assert exc.value.status_code == 400
    assert "No files attached" in str(exc.value.detail)


def test_prepare_raises_when_all_ok_on_non_draft():
    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="ok",
    )
    db = _mock_db(task_files=[SimpleNamespace(id="tf-1")], existing_run_files=[run_file])
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="awaiting_review", graph_json={}, node_states_json={})

    with pytest.raises(HTTPException) as exc:
        WorkflowService.prepare_run_files_for_execute(db, run)

    assert exc.value.status_code == 400
    assert "Re-VLM" in str(exc.value.detail)


def test_prepare_commits_upload_batches():
    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="pending",
        upload_batch_id="batch-1",
        uploaded_at=None,
        batch_committed_at=None,
    )
    db = _mock_db(task_files=[SimpleNamespace(id="tf-1")], existing_run_files=[run_file])
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        run_status="awaiting_review",
        graph_json={"nodes": [{"type": "ReceiptStyle", "data": {"tablePreset": "ap_table"}}]},
        node_states_json={},
    )

    processable, _ = WorkflowService.prepare_run_files_for_execute(db, run)

    assert len(processable) == 1
    assert processable[0].batch_committed_at is not None
    assert processable[0].uploaded_at is not None
    assert processable[0].batch_table_preset == "ap_table"
    db.commit.assert_called()


def test_prepare_uses_one_fallback_batch_for_files_missing_upload_batch_id():
    files = [
        WorkflowRunFile(
            id=f"rf-{i}",
            run_id="run-1",
            task_file_id=f"tf-{i}",
            file_status="pending",
            upload_batch_id=None,
        )
        for i in range(3)
    ]
    db = _mock_db(
        task_files=[SimpleNamespace(id=f"tf-{i}") for i in range(3)],
        existing_run_files=files,
    )
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="awaiting_review", graph_json={}, node_states_json={})

    processable, _ = WorkflowService.prepare_run_files_for_execute(db, run)

    batch_ids = {rf.upload_batch_id for rf in processable}
    assert len(batch_ids) == 1
    assert next(iter(batch_ids))


def test_recover_stuck_execution_resets_running_and_executing():
    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="running",
        error_text="stuck",
    )
    db = MagicMock()
    run_file_query = MagicMock()
    run_file_query.filter.return_value.all.return_value = [run_file]
    db.query.return_value = run_file_query
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="executing", node_states_json={})

    WorkflowService.recover_stuck_execution(db, run)

    assert run.run_status == "draft"
    assert run_file.file_status == "pending"
    assert run_file.error_text is None
    db.commit.assert_called_once()


def test_recover_stuck_execution_promotes_to_review_when_ocr_rows_exist():
    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="ok",
        result_summary_json={"tsv_rows": [{"id_number": "AP-1", "amount": 10}]},
    )
    db = MagicMock()
    run_file_query = MagicMock()
    run_file_query.filter.return_value.all.return_value = [run_file]
    db.query.return_value = run_file_query
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        run_status="executing",
        node_states_json={"manager": {"status": "failed", "detail": {"error": "503"}}},
        console_log_json=[],
    )

    WorkflowService.recover_stuck_execution(db, run)

    assert run.run_status == "awaiting_review"
    assert run.node_states_json["table"]["status"] == "active"
    assert run_file.file_status == "ok"
    db.commit.assert_called_once()


def test_promote_run_to_awaiting_review_uses_ocr_by_file_state():
    run = SimpleNamespace(
        id="run-1",
        task_id="task-1",
        run_status="draft",
        node_states_json={
            "ocr_by_file": {
                "tf-1": [{"id_number": "AP-1", "amount": 10}],
                "tf-2": [{"id_number": "AP-2", "amount": 20}],
            },
            "manager": {"status": "failed", "detail": {"error": "503"}},
        },
        console_log_json=[],
    )
    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="pending",
    )

    promoted = _promote_run_to_awaiting_review(run, [run_file])

    assert promoted is True
    assert run.run_status == "awaiting_review"
    assert run.node_states_json["table"]["status"] == "active"
    assert run.node_states_json["table"]["detail"]["row_count"] == 2


def test_remove_run_file_deletes_run_file_row():
    from types import SimpleNamespace as NS

    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="pending",
    )
    task_file = NS(
        id="tf-1",
        task_id="task-1",
        storage_path=None,
        deleted_at=None,
    )
    task = NS(id="task-1", file_count=2)

    run_file_query = MagicMock()
    run_file_query.filter.return_value.first.return_value = run_file
    task_file_query = MagicMock()
    task_file_query.filter.return_value.first.return_value = task_file
    task_query = MagicMock()
    task_query.filter.return_value.first.return_value = task

    db = MagicMock()

    def query_side_effect(model):
        name = getattr(model, "__name__", "")
        if name == "WorkflowRunFile":
            return run_file_query
        if name == "TaskFile":
            return task_file_query
        if name == "ChatTask":
            return task_query
        return MagicMock()

    db.query.side_effect = query_side_effect
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="draft", graph_json={}, node_states_json={})

    WorkflowService.remove_run_file(db, run, "tf-1")

    assert task_file.deleted_at is not None
    assert task.file_count == 1
    db.delete.assert_called_once_with(run_file)
    db.commit.assert_called_once()


def test_remove_run_file_allows_during_executing():
    from types import SimpleNamespace as NS

    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="running",
    )
    task_file = NS(
        id="tf-1",
        task_id="task-1",
        storage_path=None,
        deleted_at=None,
    )
    task = NS(id="task-1", file_count=1)

    run_file_query = MagicMock()
    run_file_query.filter.return_value.first.return_value = run_file
    task_file_query = MagicMock()
    task_file_query.filter.return_value.first.return_value = task_file
    task_query = MagicMock()
    task_query.filter.return_value.first.return_value = task

    db = MagicMock()

    def query_side_effect(model):
        name = getattr(model, "__name__", "")
        if name == "WorkflowRunFile":
            return run_file_query
        if name == "TaskFile":
            return task_file_query
        if name == "ChatTask":
            return task_query
        return MagicMock()

    db.query.side_effect = query_side_effect
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="executing")

    WorkflowService.remove_run_file(db, run, "tf-1")

    assert task_file.deleted_at is not None
    db.delete.assert_called_once_with(run_file)
    db.commit.assert_called_once()


def test_move_run_file_to_batch_updates_upload_batch_id():
    run_file = WorkflowRunFile(
        id="rf-1",
        run_id="run-1",
        task_file_id="tf-1",
        file_status="ok",
        upload_batch_id="batch-a",
        batch_table_preset="default",
    )
    target_peer = WorkflowRunFile(
        id="rf-2",
        run_id="run-1",
        task_file_id="tf-2",
        file_status="ok",
        upload_batch_id="batch-b",
        batch_table_preset="ap_table",
        batch_receipt_signal="invoice",
        uploaded_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    run_file_query = MagicMock()
    run_file_query.filter.return_value.first.side_effect = [run_file, target_peer]

    db = MagicMock()
    db.query.return_value = run_file_query
    run = SimpleNamespace(id="run-1", task_id="task-1", run_status="draft")

    WorkflowService.move_run_file_to_batch(db, run, "tf-1", "batch-b")

    assert run_file.upload_batch_id == "batch-b"
    assert run_file.batch_table_preset == "ap_table"
    assert run_file.batch_receipt_signal == "invoice"
    db.commit.assert_called_once()

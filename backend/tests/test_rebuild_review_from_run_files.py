"""Rebuild review must use only this run's files and never invent a parallel parser."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.graph.workflow_service import (
    REBUILD_FOREIGN_FILES_DETAIL,
    REBUILD_NO_FILES_DETAIL,
    RE_VLM_LOCKED_DETAIL,
    WorkflowService,
    clear_run_review_ocr_state,
    resolve_rebuild_task_file_ids,
)


def _run_file(task_file_id: str, *, summary: dict | None = None, status: str = "ok") -> SimpleNamespace:
    return SimpleNamespace(
        task_file_id=task_file_id,
        result_summary_json=summary,
        file_status=status,
        error_text="prior",
        gate_result="ok",
        run_id="run-fictional",
    )


def test_resolve_rebuild_uses_all_run_files_when_ids_omitted():
    files = [_run_file("tf-a"), _run_file("tf-b"), _run_file("tf-a")]
    assert resolve_rebuild_task_file_ids(files, None) == ["tf-a", "tf-b"]
    assert resolve_rebuild_task_file_ids(files, []) == ["tf-a", "tf-b"]


def test_resolve_rebuild_rejects_empty_run():
    with pytest.raises(HTTPException) as exc:
        resolve_rebuild_task_file_ids([], None)
    assert exc.value.status_code == 400
    assert exc.value.detail == REBUILD_NO_FILES_DETAIL


def test_resolve_rebuild_rejects_foreign_file_ids():
    files = [_run_file("tf-owned")]
    with pytest.raises(HTTPException) as exc:
        resolve_rebuild_task_file_ids(files, ["tf-owned", "tf-foreign"])
    assert exc.value.status_code == 400
    assert exc.value.detail == REBUILD_FOREIGN_FILES_DETAIL


def test_resolve_rebuild_accepts_only_owned_ids():
    files = [_run_file("tf-a"), _run_file("tf-b")]
    assert resolve_rebuild_task_file_ids(files, ["tf-b", "tf-a", "tf-b"]) == ["tf-b", "tf-a"]


def test_clear_run_review_ocr_state_drops_stale_rows():
    run = SimpleNamespace(
        run_status="awaiting_review",
        processing_mode="BANK",
        node_states_json={
            "merged_ocr": [{"source_file": "Foreign.pdf P1", "deposit": 99}],
            "ocr_by_file": {"tf-a": [{"deposit": 1}]},
            "approved_payload": {"bankTransactions": [{"deposit": 1}]},
            "vlm": {"status": "completed"},
        },
    )
    owned = _run_file(
        "tf-a",
        summary={"transactions": [{"deposit": 1, "source_file": "Foreign.pdf P1"}]},
    )
    other = _run_file("tf-b", summary={"transactions": [{"deposit": 2}]})
    clear_run_review_ocr_state(run, [owned, other], ["tf-a"])

    assert owned.result_summary_json is None
    assert owned.file_status == "pending"
    assert owned.error_text is None
    assert owned.gate_result is None
    # Untargeted file keeps its OCR until a full-run rebuild.
    assert other.result_summary_json == {"transactions": [{"deposit": 2}]}
    assert "merged_ocr" not in run.node_states_json
    assert "ocr_by_file" not in run.node_states_json
    assert "approved_payload" not in run.node_states_json
    assert run.node_states_json.get("vlm") == {"status": "completed"}


@pytest.mark.asyncio
async def test_rebuild_review_rejects_no_files_without_calling_re_vlm():
    run = SimpleNamespace(
        id="run-fictional",
        run_status="awaiting_review",
        processing_mode="BANK",
        node_states_json={},
        console_log_json=[],
    )
    db = MagicMock()

    class _Chain:
        def filter(self, *_a, **_k):
            return self

        def all(self):
            return []

    db.query.return_value = _Chain()

    with patch.object(WorkflowService, "re_vlm_files", new_callable=AsyncMock) as re_vlm:
        with pytest.raises(HTTPException) as exc:
            await WorkflowService.rebuild_review_from_run_files(db, run)
    assert exc.value.status_code == 400
    assert exc.value.detail == REBUILD_NO_FILES_DETAIL
    re_vlm.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_rebuild_review_rejects_foreign_ids_without_calling_re_vlm():
    run = SimpleNamespace(
        id="run-fictional",
        run_status="awaiting_review",
        processing_mode="BANK",
        node_states_json={"merged_ocr": [{"deposit": 1}]},
        console_log_json=[],
    )
    owned = _run_file("tf-owned", summary={"transactions": [{"deposit": 1}]})
    db = MagicMock()

    class _Chain:
        def filter(self, *_a, **_k):
            return self

        def all(self):
            return [owned]

    db.query.return_value = _Chain()

    with patch.object(WorkflowService, "re_vlm_files", new_callable=AsyncMock) as re_vlm:
        with pytest.raises(HTTPException) as exc:
            await WorkflowService.rebuild_review_from_run_files(
                db, run, task_file_ids=["tf-owned", "tf-foreign"]
            )
    assert exc.value.status_code == 400
    assert exc.value.detail == REBUILD_FOREIGN_FILES_DETAIL
    re_vlm.assert_not_called()
    # Foreign reject happens before clearing OCR.
    assert owned.result_summary_json == {"transactions": [{"deposit": 1}]}


@pytest.mark.asyncio
async def test_rebuild_review_clears_then_reuses_re_vlm_for_owned_files_only():
    run = SimpleNamespace(
        id="run-fictional",
        run_status="awaiting_review",
        processing_mode="BANK",
        node_states_json={
            "merged_ocr": [{"source_file": "Bleed.pdf P1", "deposit": 50}],
            "ocr_by_file": {"tf-a": [{"deposit": 50}]},
        },
        console_log_json=[],
        graph_json={},
    )
    rf_a = _run_file("tf-a", summary={"transactions": [{"deposit": 50}]})
    rf_b = _run_file("tf-b", summary={"transactions": [{"deposit": 7}]})
    db = MagicMock()

    class _Chain:
        def filter(self, *_a, **_k):
            return self

        def all(self):
            return [rf_a, rf_b]

    db.query.return_value = _Chain()
    rebuilt = SimpleNamespace(id="run-fictional", run_status="awaiting_review")

    with patch.object(
        WorkflowService, "re_vlm_files", new_callable=AsyncMock, return_value=rebuilt
    ) as re_vlm:
        result = await WorkflowService.rebuild_review_from_run_files(db, run)

    assert result is rebuilt
    assert rf_a.result_summary_json is None
    assert rf_b.result_summary_json is None
    assert rf_a.file_status == "pending"
    assert rf_b.file_status == "pending"
    assert "merged_ocr" not in run.node_states_json
    db.commit.assert_called()
    db.refresh.assert_called()
    re_vlm.assert_awaited_once()
    args, kwargs = re_vlm.await_args
    assert args[0] is db
    assert args[1] is run
    assert args[2] == ["tf-a", "tf-b"]
    assert kwargs.get("force_process") is True


@pytest.mark.asyncio
async def test_rebuild_review_rejects_locked_approved_run():
    run = SimpleNamespace(
        id="run-fictional",
        run_status="completed",
        processing_mode="BANK",
        node_states_json={
            "approved_payload": {"bankTransactions": [{"date": "2024-01-01", "deposit": "1"}]},
        },
        console_log_json=[],
    )
    db = MagicMock()
    with patch.object(WorkflowService, "re_vlm_files", new_callable=AsyncMock) as re_vlm:
        with pytest.raises(HTTPException) as exc:
            await WorkflowService.rebuild_review_from_run_files(db, run)
    assert exc.value.status_code == 409
    assert exc.value.detail == RE_VLM_LOCKED_DETAIL
    re_vlm.assert_not_called()
    db.query.assert_not_called()

"""Re-VLM must not rewrite extraction after Approve loads rows into modules."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.graph.workflow_service import (
    RE_VLM_LOCKED_DETAIL,
    WorkflowService,
    _run_has_locked_approved_table,
)


def _run(*, status: str, payload: dict | None, mode: str = "AP") -> SimpleNamespace:
    states = {}
    if payload is not None:
        states["approved_payload"] = payload
    return SimpleNamespace(
        id="run-fictional",
        run_status=status,
        processing_mode=mode,
        node_states_json=states,
        graph_json={},
        task_id="task-fictional",
        company_id="co-fictional",
    )


def test_locked_when_completed_with_arap_rows():
    run = _run(
        status="completed",
        payload={"arapTransactions": [{"id_number": "AP-FICTION-001", "amount": "10"}]},
    )
    assert _run_has_locked_approved_table(run) is True


def test_locked_when_saved_with_bank_rows():
    run = _run(
        status="saved",
        mode="BANK",
        payload={"bankTransactions": [{"date": "2024-01-01", "deposit": "1"}]},
    )
    assert _run_has_locked_approved_table(run) is True


def test_not_locked_awaiting_review_even_with_payload():
    run = _run(
        status="awaiting_review",
        payload={"arapTransactions": [{"id_number": "AP-FICTION-001"}]},
    )
    assert _run_has_locked_approved_table(run) is False


def test_not_locked_completed_without_rows():
    run = _run(status="completed", payload={"arapTransactions": []})
    assert _run_has_locked_approved_table(run) is False


def test_not_locked_completed_without_payload():
    run = _run(status="completed", payload=None)
    assert _run_has_locked_approved_table(run) is False


@pytest.mark.asyncio
async def test_re_vlm_files_rejects_locked_run_without_db_writes():
    run = _run(
        status="coa_running",
        payload={"arapTransactions": [{"id_number": "AP-FICTION-002", "payee": "Fictional Co"}]},
    )
    db = MagicMock()
    with pytest.raises(HTTPException) as exc:
        await WorkflowService.re_vlm_files(db, run, ["tf-fictional-1"])
    assert exc.value.status_code == 409
    assert exc.value.detail == RE_VLM_LOCKED_DETAIL
    db.query.assert_not_called()
    db.commit.assert_not_called()

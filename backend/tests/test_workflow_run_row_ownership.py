"""Regression: approve/resume must reject rows whose source files are not on the run."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.graph.workflow_service import (
    WorkflowService,
    assert_review_rows_belong_to_run,
    resolve_row_task_file_id_strict,
)


def _run_file(task_file_id: str, filename: str) -> SimpleNamespace:
    return SimpleNamespace(
        task_file_id=task_file_id,
        original_filename=filename,
        run_id="run-a",
    )


def test_strict_resolve_rejects_foreign_filename_even_on_single_file_run():
    run_files = [_run_file("tf-a", "A.pdf")]
    assert resolve_row_task_file_id_strict({"source_file": "A.pdf P1"}, run_files) == "tf-a"
    assert resolve_row_task_file_id_strict({"source_file": "A.pdf P12-R3"}, run_files) == "tf-a"
    assert resolve_row_task_file_id_strict({"source_file": "Jan.pdf P1"}, run_files) is None


def test_strict_resolve_handles_long_tab_runs_in_source_file():
    """Regression for CodeQL py/polynomial-redos: no regex on uncontrolled source_file."""
    run_files = [_run_file("tf-a", "A.pdf")]
    evil_owned = "A.pdf" + ("\t" * 20000) + "P1"
    assert resolve_row_task_file_id_strict({"source_file": evil_owned}, run_files) == "tf-a"
    evil_foreign = "Jan.pdf" + ("\t" * 20000) + "P1"
    assert resolve_row_task_file_id_strict({"source_file": evil_foreign}, run_files) is None
    # Whitespace-only padding without a page marker still matches basename.
    assert (
        resolve_row_task_file_id_strict(
            {"source_file": "A.pdf" + ("\t" * 5000)},
            run_files,
        )
        == "tf-a"
    )


def test_assert_review_rows_belong_to_run_rejects_cross_run_rows():
    run_files = [_run_file("tf-a", "A.pdf")]
    with pytest.raises(HTTPException) as exc:
        assert_review_rows_belong_to_run(
            [
                {"source_file": "Jan.pdf P1", "deposit": 1, "company_amount": 1, "company_currency": "HKD"},
                {"source_file": "Jan.pdf P2", "deposit": 2, "company_amount": 2, "company_currency": "HKD"},
            ],
            run_files,
        )
    assert exc.value.status_code == 400
    assert "aren't in this run" in str(exc.value.detail)


def test_assert_review_rows_belong_to_run_allows_matching_rows():
    run_files = [_run_file("tf-a", "A.pdf")]
    assert_review_rows_belong_to_run(
        [{"source_file": "A.pdf P1", "deposit": 13}],
        run_files,
    )


def test_assert_review_rows_belong_to_run_empty_ok():
    assert_review_rows_belong_to_run([], [_run_file("tf-a", "A.pdf")])
    assert_review_rows_belong_to_run(None, [_run_file("tf-a", "A.pdf")])


@pytest.mark.asyncio
async def test_resume_run_rejects_bank_rows_from_another_run():
    run = SimpleNamespace(
        id="run-a",
        task_id="task-a",
        company_id="co-1",
        processing_mode="BANK",
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
    )
    task = SimpleNamespace(id="task-a")
    run_file = _run_file("tf-a", "A.pdf")

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
        if name == "ChatTask":
            return _Chain([task])
        if name == "WorkflowRunFile":
            return _Chain([run_file])
        if name == "TaskFile":
            return _Chain(
                [SimpleNamespace(id="tf-a", original_filename="A.pdf")]
            )
        return _Chain([])

    db.query.side_effect = query_side_effect

    foreign_payload = {
        "bankTransactions": [
            {
                "source_file": "Jan.pdf P1",
                "deposit": 100,
                "currency": "HKD",
                "company_currency": "HKD",
                "company_amount": 100,
                "exchange_rate": 1,
            }
        ]
    }

    with pytest.raises(HTTPException) as exc:
        await WorkflowService.resume_run(
            db,
            run,
            approved_payload=foreign_payload,
            skip_coa=False,
            user_id="user-1",
        )
    assert exc.value.status_code == 400
    assert "aren't in this run" in str(exc.value.detail)
    assert "approved_payload" not in (run.node_states_json or {})


@pytest.mark.asyncio
async def test_resume_run_accepts_rows_for_current_run_files():
    run = SimpleNamespace(
        id="run-a",
        task_id="task-a",
        company_id="co-1",
        processing_mode="BANK",
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
    )
    task = SimpleNamespace(id="task-a")
    run_file = _run_file("tf-a", "A.pdf")

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
        if name == "ChatTask":
            return _Chain([task])
        if name == "WorkflowRunFile":
            return _Chain([run_file])
        if name == "TaskFile":
            return _Chain(
                [SimpleNamespace(id="tf-a", original_filename="A.pdf")]
            )
        return _Chain([])

    db.query.side_effect = query_side_effect

    payload = {
        "bankTransactions": [
            {
                "source_file": "A.pdf P1",
                "deposit": 13,
                "currency": "HKD",
                "company_currency": "HKD",
                "company_amount": 13,
                "exchange_rate": 1,
            }
        ]
    }

    with patch(
        "app.graph.executor.run_workflow_graph", new_callable=AsyncMock
    ) as mock_graph, patch.object(
        WorkflowService, "_transfer_to_module", new_callable=AsyncMock
    ):
        result = await WorkflowService.resume_run(
            db,
            run,
            approved_payload=payload,
            skip_coa=False,
            user_id="user-1",
        )

    assert result is run
    assert run.node_states_json["approved_payload"] == payload
    mock_graph.assert_awaited_once()

"""Approve/resume blocked while dense empty-extract pages remain (fiction-only)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.graph.workflow_service import WorkflowService
from app.services.bank_empty_extract import EMPTY_EXTRACT_TITLE


def _resume_db_with_empty_extract_file() -> MagicMock:
    task = SimpleNamespace(id="task-fictional")
    run_file = SimpleNamespace(
        task_file_id="tf-fictional",
        original_filename="Fictional-Statement.pdf",
        run_id="run-fictional",
        result_summary_json={
            "pages": [
                {"page": 1, "status": "success"},
                {
                    "page": 2,
                    "status": "needs_retry",
                    "empty_extract": True,
                    "empty_extract_acknowledged": False,
                    "empty_extract_auto_retried": True,
                },
            ]
        },
    )
    task_file = SimpleNamespace(id="tf-fictional", original_filename="Fictional-Statement.pdf")
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
            return _Chain([task_file])
        return _Chain([])

    db.query.side_effect = query_side_effect
    return db


@pytest.mark.asyncio
async def test_resume_rejects_pending_empty_extract_pages() -> None:
    run = SimpleNamespace(
        id="run-fictional",
        task_id="task-fictional",
        company_id="co-fictional",
        processing_mode="BANK",
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
    )
    db = _resume_db_with_empty_extract_file()
    payload = {
        "bankTransactions": [
            {
                "source_file": "Fictional-Statement.pdf P1",
                "date": "2020-08-01",
                "currency": "HKD",
                "deposit": None,
                "withdrawal": None,
                "balance": 1000.0,
                "description": "Fictional opening",
                "company_currency": "HKD",
                "company_amount": 1000.0,
                "exchange_rate": 1,
            }
        ]
    }

    with patch("app.graph.executor.run_workflow_graph", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as ei:
            await WorkflowService.resume_run(
                db,
                run,
                approved_payload=payload,
                skip_coa=False,
                user_id="user-fictional",
            )
    assert ei.value.status_code == 400
    detail = str(ei.value.detail)
    assert EMPTY_EXTRACT_TITLE in detail or "need retry" in detail.lower()

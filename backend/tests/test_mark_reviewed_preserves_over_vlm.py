"""Mark-as-reviewed must win over a late VLM finish (fiction-only)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.graph.ocr_partial_merge import merge_partial_ocr_summary, upsert_ocr_pages
from app.graph.workflow_service import WorkflowService
from app.services.bank_empty_extract import (
    EMPTY_EXTRACT_GATE,
    REVIEWED_NO_ROWS_GATE,
    REVIEWED_NO_ROWS_LABEL,
    REVIEWED_NO_ROWS_STATUS,
    format_live_output_page_statuses,
    mark_page_reviewed_with_no_rows,
    merge_result_preserving_reviewed,
    page_has_pending_empty_extract,
    preserve_user_reviewed_pages,
)


def _amber_page(page: int, *, auto_retried: bool = True) -> dict:
    return {
        "page": page,
        "status": "needs_retry",
        "empty_extract": True,
        "empty_extract_acknowledged": False,
        "empty_extract_auto_retried": auto_retried,
    }


def test_preserve_user_reviewed_pages_keeps_mark_over_late_empty_extract() -> None:
    existing = [
        mark_page_reviewed_with_no_rows(_amber_page(1)),
        _amber_page(2),
    ]
    incoming = [
        _amber_page(1),  # late VLM still says empty-extract on page 1
        {"page": 2, "status": "success", "empty_extract": False},
    ]
    merged = preserve_user_reviewed_pages(existing, incoming)
    assert merged[0]["page"] == 1
    assert merged[0]["status"] == REVIEWED_NO_ROWS_STATUS
    assert merged[0]["empty_extract_acknowledged"] is True
    assert page_has_pending_empty_extract(merged[0]) is False
    assert merged[1]["page"] == 2
    assert merged[1]["status"] == "success"


def test_merge_result_preserving_reviewed_survives_full_vlm_replace() -> None:
    prior = {
        "pages": [
            mark_page_reviewed_with_no_rows(_amber_page(1)),
            _amber_page(2),
        ]
    }
    late_vlm = {
        "pages": [
            {
                "page": 1,
                "status": "needs_retry",
                "empty_extract": True,
                "empty_extract_acknowledged": False,
            },
            {"page": 2, "status": "success"},
        ],
        "ocr_job_outcome": "partial",
    }
    merged = merge_result_preserving_reviewed(prior, late_vlm)
    assert merged["pages"][0]["empty_extract_acknowledged"] is True
    assert merged["pages"][0]["status"] == REVIEWED_NO_ROWS_STATUS
    assert page_has_pending_empty_extract(merged["pages"][0]) is False
    assert merged["reviewed_no_rows_summary"]["gate"] == REVIEWED_NO_ROWS_GATE


def test_upsert_ocr_pages_mark_wins_over_incoming_key() -> None:
    existing = [mark_page_reviewed_with_no_rows(_amber_page(1))]
    incoming = [
        {
            "page": 1,
            "receipt_instance_id": "rid-new-fictional",
            "status": "needs_retry",
            "empty_extract": True,
        }
    ]
    out = upsert_ocr_pages(existing, incoming)
    assert len(out) == 1
    assert out[0]["empty_extract_acknowledged"] is True
    assert out[0]["status"] == REVIEWED_NO_ROWS_STATUS


def test_merge_partial_ocr_summary_preserves_mark() -> None:
    existing = {
        "pages": [mark_page_reviewed_with_no_rows(_amber_page(1)), _amber_page(2)],
    }
    incoming = {
        "pages": [_amber_page(1), {"page": 2, "status": "running"}],
        "partial": True,
    }
    merged = merge_partial_ocr_summary(existing, incoming)
    assert merged["pages"][0]["empty_extract_acknowledged"] is True
    assert merged["partial"] is True


def test_live_output_mixed_per_page_statuses() -> None:
    pages = [
        mark_page_reviewed_with_no_rows(_amber_page(1)),
        _amber_page(2),
    ]
    text = format_live_output_page_statuses(pages)
    assert text == f"Page 1: {REVIEWED_NO_ROWS_LABEL} · Page 2: needs retry"


def test_refresh_file_status_mixed_pages_keeps_warning_and_per_page_detail() -> None:
    run_file = SimpleNamespace(
        file_status="warning",
        gate_result=EMPTY_EXTRACT_GATE,
        error_text="old",
        result_summary_json={
            "pages": [
                mark_page_reviewed_with_no_rows(_amber_page(1)),
                _amber_page(2),
            ],
            "total_pages": 2,
        },
    )
    WorkflowService._refresh_bank_file_status_from_pages(run_file)
    assert run_file.file_status == "warning"
    assert run_file.gate_result == EMPTY_EXTRACT_GATE
    assert f"Page 1: {REVIEWED_NO_ROWS_LABEL}" in (run_file.error_text or "")
    assert "Page 2: needs retry" in (run_file.error_text or "")
    assert "live_page_statuses" in run_file.result_summary_json


def test_refresh_file_status_all_reviewed_is_neutral_not_passed_warning() -> None:
    run_file = SimpleNamespace(
        file_status="warning",
        gate_result=EMPTY_EXTRACT_GATE,
        error_text="old",
        result_summary_json={
            "pages": [
                mark_page_reviewed_with_no_rows(_amber_page(1)),
                mark_page_reviewed_with_no_rows(_amber_page(2)),
            ],
        },
    )
    WorkflowService._refresh_bank_file_status_from_pages(run_file)
    assert run_file.file_status == "ok"
    assert run_file.gate_result == REVIEWED_NO_ROWS_GATE
    assert REVIEWED_NO_ROWS_LABEL in (run_file.error_text or "")
    assert EMPTY_EXTRACT_GATE != run_file.gate_result


def test_mark_bank_page_reviewed_persists_acknowledged_flag() -> None:
    pages = [
        {"page": 1, "status": "success"},
        _amber_page(2, auto_retried=True),
    ]
    run_file = SimpleNamespace(
        id="rf-fictional",
        run_id="run-fictional",
        task_file_id="tf-fictional",
        file_status="warning",
        gate_result=EMPTY_EXTRACT_GATE,
        error_text="pending",
        result_summary_json={"pages": pages},
    )
    run = SimpleNamespace(
        id="run-fictional",
        processing_mode="BANK",
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
    )

    class _Chain:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_a, **_k):
            return self

        def first(self):
            return self._rows[0] if self._rows else None

        def all(self):
            return self._rows

    db = MagicMock()
    db.query.side_effect = lambda model: _Chain([run_file])
    db.commit = MagicMock()
    db.refresh = MagicMock()
    db.add = MagicMock()

    out = WorkflowService.mark_bank_page_reviewed_with_no_rows(
        db, run, "tf-fictional", 2
    )
    assert out is run
    stored = run_file.result_summary_json["pages"]
    page2 = next(p for p in stored if p["page"] == 2)
    assert page2["empty_extract_acknowledged"] is True
    assert page2["status"] == REVIEWED_NO_ROWS_STATUS
    assert page_has_pending_empty_extract(page2) is False
    assert run_file.gate_result == REVIEWED_NO_ROWS_GATE
    assert REVIEWED_NO_ROWS_LABEL in (run_file.error_text or "")
    db.commit.assert_called()

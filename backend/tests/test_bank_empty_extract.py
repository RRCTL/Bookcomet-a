"""Dense OCR + empty VLM extract → soft fail / retry (fiction-only fixtures)."""
from __future__ import annotations

from app.api.ocr import recompute_ocr_job_outcome_from_pages
from app.services.bank_empty_extract import (
    EMPTY_EXTRACT_BODY,
    EMPTY_EXTRACT_GATE,
    EMPTY_EXTRACT_MARK_REVIEWED_ACTION,
    EMPTY_EXTRACT_MARK_REVIEWED_CONFIRM,
    EMPTY_EXTRACT_RETRY_ACTION,
    EMPTY_EXTRACT_STATUS,
    EMPTY_EXTRACT_TITLE,
    apply_empty_extract_flags,
    assess_bank_page_extract,
    collect_pending_empty_extract_pages,
    count_activity_rows,
    mark_page_reviewed_with_no_rows,
    page_has_pending_empty_extract,
    summarize_empty_extract_for_file,
)

# Fiction-only dense OCR: looks like an activity table, never real bank data.
_FICTIONAL_DENSE_OCR = "\n".join(
    [
        "Fictional Bank Statement Activity Table",
        "Date Particulars Deposit Withdrawal Balance",
        "日期 備註 存入 提取 結餘",
        *[f"ROW{i} fictional activity line placeholder text" for i in range(1, 80)],
    ]
)


def test_ui_copy_is_extract_miss_not_blank_page() -> None:
    assert EMPTY_EXTRACT_TITLE == "Extraction returned no rows — retry this page"
    assert "not treated as blank" in EMPTY_EXTRACT_BODY
    assert EMPTY_EXTRACT_RETRY_ACTION == "Retry this page"
    assert EMPTY_EXTRACT_MARK_REVIEWED_ACTION == "Mark as reviewed with no rows"
    assert "really has no transactions" in EMPTY_EXTRACT_MARK_REVIEWED_CONFIRM
    # Never imply the PDF page itself is blank.
    assert "blank page" not in EMPTY_EXTRACT_TITLE.lower()
    assert "no transactions on this page" not in EMPTY_EXTRACT_TITLE.lower()


def test_dense_ocr_short_empty_tsv_flags_needs_retry() -> None:
    assessment = assess_bank_page_extract(
        ocr_text=_FICTIONAL_DENSE_OCR,
        lines_count=120,
        ai_enhanced={
            "raw_text": "No.\t日期\t存入\t提取\n",
            "tsv_rows": [],
        },
    )
    assert assessment.is_dense is True
    assert assessment.activity_row_count == 0
    assert assessment.needs_retry is True
    assert assessment.reason == "dense_ocr_zero_activity"


def test_opening_balance_only_rows_are_not_activity() -> None:
    rows = [
        {
            "日期": "2020-08-01",
            "備註": "Balance B/F 承上結餘",
            "存入": "",
            "提取": "",
            "原幣結餘": "1000.00",
            "賬戶類型": "HKD CURRENT",
        },
        {
            "日期": "2020-08-01",
            "備註": "Opening balance",
            "存入": "",
            "提取": "",
            "原幣結餘": "500.00",
            "賬戶類型": "HKD STATEMENT SAVINGS",
        },
    ]
    assert count_activity_rows(rows) == 0


def test_activity_row_with_amount_counts() -> None:
    rows = [
        {
            "日期": "2020-08-11",
            "備註": "Fictional FPS transfer",
            "存入": "125.50",
            "提取": "",
        }
    ]
    assert count_activity_rows(rows) == 1
    assessment = assess_bank_page_extract(
        ocr_text=_FICTIONAL_DENSE_OCR,
        lines_count=120,
        ai_enhanced={"tsv_rows": rows},
    )
    assert assessment.needs_retry is False


def test_sparse_cover_page_not_flagged() -> None:
    sparse = "Portfolio summary cover\nAccount overview only\n"
    assessment = assess_bank_page_extract(
        ocr_text=sparse,
        lines_count=3,
        ai_enhanced={"raw_text": "", "tsv_rows": []},
    )
    assert assessment.is_dense is False
    assert assessment.needs_retry is False


def test_apply_flags_and_mark_reviewed() -> None:
    assessment = assess_bank_page_extract(
        ocr_text=_FICTIONAL_DENSE_OCR,
        lines_count=100,
        ai_enhanced={"raw_text": "x" * 40, "tsv_rows": []},
    )
    page: dict = {"page": 2, "ai_enhanced": {"tsv_rows": []}}
    apply_empty_extract_flags(page, assessment, auto_retried=True)
    assert page["status"] == EMPTY_EXTRACT_STATUS
    assert page["empty_extract"] is True
    assert page["empty_extract_title"] == EMPTY_EXTRACT_TITLE
    assert page["empty_extract_body"] == EMPTY_EXTRACT_BODY
    assert page["empty_extract_auto_retried"] is True
    assert page_has_pending_empty_extract(page) is True

    mark_page_reviewed_with_no_rows(page)
    assert page_has_pending_empty_extract(page) is False
    assert page["empty_extract_acknowledged"] is True
    assert page["status"] == "reviewed_no_rows"
    assert page["empty_extract_message"] == "Reviewed — no rows"


def test_ocr_job_outcome_partial_when_extract_miss() -> None:
    pages = [
        {"page": 1, "status": "success"},
        {
            "page": 2,
            "status": EMPTY_EXTRACT_STATUS,
            "empty_extract": True,
            "empty_extract_acknowledged": False,
        },
        {
            "page": 3,
            "status": EMPTY_EXTRACT_STATUS,
            "empty_extract": True,
            "empty_extract_acknowledged": False,
        },
    ]
    assert recompute_ocr_job_outcome_from_pages(pages) == "partial"
    summary = summarize_empty_extract_for_file(pages)
    assert summary is not None
    assert summary["gate"] == EMPTY_EXTRACT_GATE
    assert summary["pending_pages"] == [2, 3]
    assert EMPTY_EXTRACT_TITLE in summary["message"]


def test_collect_pending_skips_acknowledged() -> None:
    pages = [
        {
            "page": 2,
            "status": EMPTY_EXTRACT_STATUS,
            "empty_extract": True,
            "empty_extract_acknowledged": True,
        }
    ]
    assert collect_pending_empty_extract_pages(pages) == []
    assert recompute_ocr_job_outcome_from_pages(pages) == "ok"

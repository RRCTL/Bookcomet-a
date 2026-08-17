from __future__ import annotations

from app.api.ocr import recompute_ocr_job_outcome_from_pages
from app.api.ocr import _classify_ocr_error_code, _scenario_d_termination_reason


def test_recompute_outcome_partial() -> None:
    pages = [
        {"page": 1, "status": "success"},
        {"page": 2, "status": "error"},
    ]
    assert recompute_ocr_job_outcome_from_pages(pages) == "partial"


def test_recompute_outcome_all_error() -> None:
    pages = [{"page": 1, "status": "error"}]
    assert recompute_ocr_job_outcome_from_pages(pages) == "failed"


def test_classify_ocr_error_code() -> None:
    assert _classify_ocr_error_code(RuntimeError("OCR_HTTP_429: upstream")) == "VLM_RATE_LIMIT"
    assert _classify_ocr_error_code(RuntimeError("OCR_EMPTY_CONTENT: empty")) == "VLM_EMPTY_CONTENT"
    assert _classify_ocr_error_code(RuntimeError("OCR_REQUEST_ERROR: timeout")) == "VLM_REQUEST_FAILED"


def test_scenario_d_termination_reason() -> None:
    assert _scenario_d_termination_reason(
        consecutive_failures=0,
        failed_pages=0,
        observed_pages=3,
        saw_rate_limit=True,
    ) == "rate_limited"

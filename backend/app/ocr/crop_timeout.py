"""Per-crop OCR deadline, timeout stubs, and single-page persist payloads."""

from __future__ import annotations

import os
from typing import Any

from app.ocr.vlm_layout_detect import receipt_instance_id
from app.services.extraction_validation import attach_receipt_region_provenance

OCR_TIMEOUT_MEMO = "[OCR timeout]"
VLM_CROP_TIMEOUT_CODE = "VLM_CROP_TIMEOUT"
OCR_TIMEOUT_FLAG = "ocr_timeout"
_DEFAULT_CROP_TIMEOUT_S = 120.0
_DEFAULT_HTTP_MAX_RETRIES = 3


def _positive_float(raw: str) -> float | None:
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return None


def _positive_int(raw: Any) -> int | None:
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return None


def resolve_ap_crop_ocr_timeout_s() -> float:
    """AP_CROP_OCR_TIMEOUT_S, else VLM_READ_TIMEOUT → VLM_TIMEOUT → 120."""
    override = (os.getenv("AP_CROP_OCR_TIMEOUT_S") or "").strip()
    if override:
        parsed = _positive_float(override)
        if parsed is not None:
            return parsed
    for name in ("VLM_READ_TIMEOUT", "VLM_TIMEOUT"):
        raw = (os.getenv(name) or "").strip()
        if not raw:
            continue
        parsed = _positive_float(raw)
        if parsed is not None:
            return parsed
    return _DEFAULT_CROP_TIMEOUT_S


def resolve_vlm_http_max_retries(ocr_options: dict | None = None) -> int:
    """ocr_options.http_max_retries, else VLM_HTTP_MAX_RETRIES, else 3."""
    options = ocr_options or {}
    if options.get("http_max_retries") is not None:
        parsed = _positive_int(options.get("http_max_retries"))
        if parsed is not None:
            return parsed
    raw = (os.getenv("VLM_HTTP_MAX_RETRIES") or "").strip()
    if raw:
        parsed = _positive_int(raw)
        if parsed is not None:
            return parsed
    return _DEFAULT_HTTP_MAX_RETRIES


def build_crop_stub_tsv_row(
    *,
    receipt_bbox: dict[str, int] | None,
    pdf_page_num: int,
    receipt_index: int,
    parent_image_size: tuple[int, int] | None,
    vlm_mode: bool,
    extra_flags: list[str] | None = None,
    memo: str | None = None,
) -> dict[str, Any]:
    flags = list(extra_flags or [])
    if "incomplete_extraction" not in flags:
        flags.append("incomplete_extraction")
    row: dict[str, Any] = {
        "needs_review": True,
        "validation_flags": flags,
        "amount": "",
    }
    if memo:
        row["memo"] = memo
    attach_receipt_region_provenance(
        row,
        receipt_bbox=receipt_bbox,
        pdf_page_num=pdf_page_num,
        parent_image_size=parent_image_size,
        segmentation_mode="vlm_detect" if vlm_mode else None,
        segmentation_source="vlm_layout" if vlm_mode else None,
        crop_status="verified_vlm_crop" if vlm_mode else None,
        receipt_instance_id=receipt_instance_id(pdf_page_num, receipt_index),
    )
    return row


def build_crop_failure_page(
    *,
    pdf_page_num: int,
    receipt_index: int,
    receipt_bbox: dict[str, int] | None,
    parent_image_size: tuple[int, int] | None,
    vlm_mode: bool,
    error_code: str,
    error_detail: str,
    extra_flags: list[str] | None = None,
    memo: str | None = None,
    seg_source: str = "vlm_layout",
) -> dict[str, Any]:
    stub = build_crop_stub_tsv_row(
        receipt_bbox=receipt_bbox,
        pdf_page_num=pdf_page_num,
        receipt_index=receipt_index,
        parent_image_size=parent_image_size,
        vlm_mode=vlm_mode,
        extra_flags=extra_flags,
        memo=memo,
    )
    return {
        "page": pdf_page_num,
        "receipt_index": receipt_index,
        "receipt_instance_id": receipt_instance_id(pdf_page_num, receipt_index),
        "status": "error",
        "error_code": error_code,
        "error_detail": str(error_detail)[:4000],
        "text": "",
        "lines_count": 0,
        "extracted_fields": {},
        "field_confidence": 0.0,
        "ai_enhanced": {
            "output_format": "tsv",
            "tsv_rows": [stub],
            "ai_processed": True,
        },
        "receipt_bbox": receipt_bbox,
        "segmentation_mode": "vlm_detect" if vlm_mode else "opencv",
        "segmentation_source": "vlm_layout" if vlm_mode else seg_source,
        "crop_status": "verified_vlm_crop" if vlm_mode else None,
        "needs_review": True,
    }


def build_crop_timeout_page(
    *,
    pdf_page_num: int,
    receipt_index: int,
    receipt_bbox: dict[str, int] | None,
    parent_image_size: tuple[int, int] | None = None,
    vlm_mode: bool = True,
    seg_source: str = "vlm_layout",
) -> dict[str, Any]:
    return build_crop_failure_page(
        pdf_page_num=pdf_page_num,
        receipt_index=receipt_index,
        receipt_bbox=receipt_bbox,
        parent_image_size=parent_image_size,
        vlm_mode=vlm_mode,
        error_code=VLM_CROP_TIMEOUT_CODE,
        error_detail="Crop OCR exceeded the configured per-crop deadline",
        extra_flags=[OCR_TIMEOUT_FLAG],
        memo=OCR_TIMEOUT_MEMO,
        seg_source=seg_source,
    )


def public_page_for_crop_outcome(
    outcome: Any,
    *,
    pdf_page_num: int,
    receipt_index: int,
    receipt_bbox: dict[str, int] | None,
    parent_image_size: tuple[int, int] | None = None,
    vlm_mode: bool = True,
    seg_source: str = "vlm_layout",
) -> dict[str, Any]:
    if isinstance(outcome, TimeoutError):
        return build_crop_timeout_page(
            pdf_page_num=pdf_page_num,
            receipt_index=receipt_index,
            receipt_bbox=receipt_bbox,
            parent_image_size=parent_image_size,
            vlm_mode=vlm_mode,
            seg_source=seg_source,
        )
    if isinstance(outcome, BaseException):
        return build_crop_failure_page(
            pdf_page_num=pdf_page_num,
            receipt_index=receipt_index,
            receipt_bbox=receipt_bbox,
            parent_image_size=parent_image_size,
            vlm_mode=vlm_mode,
            error_code="CROP_OCR_FAILED",
            error_detail=str(outcome)[:4000] or type(outcome).__name__,
            seg_source=seg_source,
        )
    if isinstance(outcome, dict):
        page = dict(outcome)
        page.setdefault("status", "success")
        page.setdefault("page", pdf_page_num)
        page.setdefault("receipt_index", receipt_index)
        page.setdefault(
            "receipt_instance_id",
            receipt_instance_id(pdf_page_num, receipt_index),
        )
        if receipt_bbox is not None:
            page.setdefault("receipt_bbox", receipt_bbox)
        return page
    return build_crop_failure_page(
        pdf_page_num=pdf_page_num,
        receipt_index=receipt_index,
        receipt_bbox=receipt_bbox,
        parent_image_size=parent_image_size,
        vlm_mode=vlm_mode,
        error_code="CROP_OCR_FAILED",
        error_detail="Unknown crop outcome",
        seg_source=seg_source,
    )


def build_crop_partial_snapshot(
    *,
    trace_id: str,
    filename: str,
    processing_mode: str,
    page: dict[str, Any],
    provider: str | None = None,
) -> dict[str, Any]:
    """Single-crop envelope. Omits total_pages so upsert keeps the PDF page count."""
    status = page.get("status")
    return {
        "trace_id": trace_id,
        "filename": filename,
        "document_type": "multi_page_pdf",
        "pages": [page],
        "ocr_job_outcome": "failed" if status == "error" else "ok",
        "processing_mode": processing_mode,
        "provider": provider or "multi_page_processor",
    }


def crop_outcomes_to_persist_payloads(
    outcomes: list[tuple[int, Any, dict[str, int] | None]],
    *,
    pdf_page_num: int,
    trace_id: str,
    filename: str,
    processing_mode: str,
    parent_image_size: tuple[int, int] | None = None,
    vlm_mode: bool = True,
    provider: str | None = None,
) -> list[dict[str, Any]]:
    """Map finished crop outcomes to one persist payload each (no network)."""
    payloads: list[dict[str, Any]] = []
    for receipt_index, outcome, bbox in outcomes:
        page = public_page_for_crop_outcome(
            outcome,
            pdf_page_num=pdf_page_num,
            receipt_index=receipt_index,
            receipt_bbox=bbox,
            parent_image_size=parent_image_size,
            vlm_mode=vlm_mode,
        )
        payloads.append(
            build_crop_partial_snapshot(
                trace_id=trace_id,
                filename=filename,
                processing_mode=processing_mode,
                page=page,
                provider=provider,
            )
        )
    return payloads

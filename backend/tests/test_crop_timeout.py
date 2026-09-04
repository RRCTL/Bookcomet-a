"""Synthetic per-crop timeout helpers and live persist (no network, no real receipts)."""

from __future__ import annotations

import asyncio

import pytest

from app.ocr.crop_timeout import (
    OCR_TIMEOUT_FLAG,
    OCR_TIMEOUT_MEMO,
    VLM_CROP_TIMEOUT_CODE,
    build_crop_timeout_page,
    crop_outcomes_to_persist_payloads,
    resolve_ap_crop_ocr_timeout_s,
)
from app.ocr.providers import resolve_vlm_http_max_retries


def test_resolve_crop_timeout_prefers_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AP_CROP_OCR_TIMEOUT_S", "90")
    monkeypatch.setenv("VLM_READ_TIMEOUT", "360")
    monkeypatch.setenv("VLM_TIMEOUT", "30")
    assert resolve_ap_crop_ocr_timeout_s() == 90.0


def test_resolve_crop_timeout_follows_vlm_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AP_CROP_OCR_TIMEOUT_S", raising=False)
    monkeypatch.setenv("VLM_READ_TIMEOUT", "120")
    monkeypatch.delenv("VLM_TIMEOUT", raising=False)
    assert resolve_ap_crop_ocr_timeout_s() == 120.0


def test_resolve_crop_timeout_default_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AP_CROP_OCR_TIMEOUT_S", raising=False)
    monkeypatch.delenv("VLM_READ_TIMEOUT", raising=False)
    monkeypatch.delenv("VLM_TIMEOUT", raising=False)
    assert resolve_ap_crop_ocr_timeout_s() == 240.0


def test_resolve_http_max_retries_options_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VLM_HTTP_MAX_RETRIES", raising=False)
    assert resolve_vlm_http_max_retries() == 3
    assert resolve_vlm_http_max_retries({"http_max_retries": 1}) == 1
    monkeypatch.setenv("VLM_HTTP_MAX_RETRIES", "2")
    assert resolve_vlm_http_max_retries() == 2


def test_timeout_stub_row_is_reviewable() -> None:
    page = build_crop_timeout_page(
        pdf_page_num=6,
        receipt_index=2,
        receipt_bbox={"x": 10, "y": 20, "w": 30, "h": 40},
        parent_image_size=(100, 80),
    )
    assert page["status"] == "error"
    assert page["error_code"] == VLM_CROP_TIMEOUT_CODE
    assert page["receipt_instance_id"] == "p6-r02"
    assert page["receipt_bbox"] == {"x": 10, "y": 20, "w": 30, "h": 40}
    row = page["ai_enhanced"]["tsv_rows"][0]
    assert row["amount"] == ""
    assert row["memo"] == OCR_TIMEOUT_MEMO
    assert row["needs_review"] is True
    assert OCR_TIMEOUT_FLAG in row["validation_flags"]


def test_three_crop_outcomes_emit_three_single_page_snapshots() -> None:
    ok1 = {
        "page": 6,
        "receipt_index": 1,
        "receipt_instance_id": "p6-r01",
        "status": "success",
        "ai_enhanced": {"tsv_rows": [{"voucher_no": "C1"}]},
    }
    ok3 = {
        "page": 6,
        "receipt_index": 3,
        "receipt_instance_id": "p6-r03",
        "status": "success",
        "ai_enhanced": {"tsv_rows": [{"voucher_no": "C3"}]},
    }
    payloads = crop_outcomes_to_persist_payloads(
        [
            (1, ok1, {"x": 0, "y": 0, "w": 10, "h": 10}),
            (2, TimeoutError("unit-timeout"), {"x": 20, "y": 0, "w": 10, "h": 10}),
            (3, ok3, {"x": 40, "y": 0, "w": 10, "h": 10}),
        ],
        pdf_page_num=6,
        trace_id="t-synth",
        filename="synthetic.pdf",
        processing_mode="AP",
    )
    assert len(payloads) == 3
    assert all(len(p["pages"]) == 1 for p in payloads)
    assert "total_pages" not in payloads[0]
    assert payloads[0]["pages"][0]["receipt_instance_id"] == "p6-r01"
    timeout_page = payloads[1]["pages"][0]
    assert timeout_page["error_code"] == VLM_CROP_TIMEOUT_CODE
    assert timeout_page["ai_enhanced"]["tsv_rows"][0]["memo"] == OCR_TIMEOUT_MEMO
    assert payloads[2]["pages"][0]["receipt_instance_id"] == "p6-r03"


@pytest.mark.asyncio
async def test_crop_loop_persists_each_result_including_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AP_CROP_OCR_TIMEOUT_S", "0.15")
    from PIL import Image

    from app.api import ocr

    page_png = tmp_path / "synthetic_page.png"
    Image.new("RGB", (180, 80), (255, 255, 255)).save(page_png)

    async def three_boxes(*_a, **_k):
        return [
            {"x": 0, "y": 0, "w": 40, "h": 50},
            {"x": 50, "y": 0, "w": 40, "h": 50},
            {"x": 100, "y": 0, "w": 40, "h": 50},
        ]

    persists: list[dict] = []

    async def fake_persist(**kwargs):
        persists.append(kwargs.get("result_json") or {})

    async def fake_extract(**kwargs):
        page_num = kwargs.get("page_num")
        if page_num == 2:
            await asyncio.sleep(2)
        return {
            "output_format": "tsv",
            "ai_processed": True,
            "tsv_rows": [{"voucher_no": f"C{page_num}"}],
        }

    async def passthrough_merge(*, ai_primary, **_k):
        return ai_primary

    class _FakeOcr:
        async def recognize(self, *_a, **_k):
            raise AssertionError("structured-only must skip pass-1 recognize")

    class _FakeFilter:
        def filter_and_extract(self, _result):
            return {"fields": {}, "overall_confidence": 0.0, "missing_fields": []}

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", three_boxes)
    monkeypatch.setattr(ocr, "_ocr_service", _FakeOcr())
    monkeypatch.setattr(ocr, "_filtering_pipeline", _FakeFilter())
    monkeypatch.setattr(ocr, "_extract_ar_ap_ai_fields_routed", fake_extract)
    monkeypatch.setattr(ocr, "_ap_apply_cross_vlm_merge_if_configured", passthrough_merge)
    monkeypatch.setattr(ocr, "_persist_ocr_partial_snapshot", fake_persist)
    monkeypatch.setattr(ocr._receipt_image_quality, "quality_enabled", lambda: False)

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(page_png),
        trace_id="t-synth",
        filename="synthetic.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="settings-vlm",
        ocr_prompt_override=None,
        processing_mode="AP",
        confirmed=True,
        pdf_page_num=6,
    )
    assert result is not None
    assert len(persists) == 3
    assert all(len(p.get("pages") or []) == 1 for p in persists)
    pages = result.get("pages") or []
    assert len(pages) == 3
    by_idx = {p.get("receipt_index"): p for p in pages}
    assert by_idx[1]["ai_enhanced"]["tsv_rows"][0]["voucher_no"] == "C1"
    assert by_idx[2]["error_code"] == VLM_CROP_TIMEOUT_CODE
    assert by_idx[2]["ai_enhanced"]["tsv_rows"][0]["memo"] == OCR_TIMEOUT_MEMO
    assert by_idx[3]["ai_enhanced"]["tsv_rows"][0]["voucher_no"] == "C3"

"""Unit tests for AP cross-VLM call gating and cheque orientation reuse."""
import dataclasses

import pytest
from unittest.mock import AsyncMock, patch

from app.api import ocr


def _primary(confidence: str = "0.95", **extra) -> dict:
    return {
        "output_format": "tsv",
        "tsv_rows": [{"amount": "100", "confidence": confidence}],
        "ai_processed": True,
        "extraction_source": "cheque_vlm_json",
        **extra,
    }


async def _run_merge(ai_primary: dict, cheque_probe: dict | None = None) -> dict:
    return await ocr._ap_apply_cross_vlm_merge_if_configured(
        processing_mode="AP",
        primary_model="qwen3.5-35b-a3b",
        ai_primary=ai_primary,
        ocr_text="text",
        img_path="/tmp/x.png",
        page_num=1,
        ocr_provider_name="qwen",
        image_options=None,
        ocr_lines=None,
        cheque_probe=cheque_probe,
    )


def _cross_settings(**overrides):
    values = {
        "ap_cross_vlm_model": "doubao-cross",
        "ap_auto_cross_verify_enabled": True,
        "ap_auto_cross_verify_skip_primary_confidence": 0.0,
        "ap_auto_cross_verify_confidence_threshold": 0.0,
        "ap_auto_cross_verify_timeout_ms": 120000,
        **overrides,
    }
    return patch.object(ocr, "settings", dataclasses.replace(ocr.settings, **values))


@pytest.mark.asyncio
async def test_skip_cross_when_primary_confident():
    primary = _primary(confidence="0.95")
    with _cross_settings(ap_auto_cross_verify_skip_primary_confidence=0.9):
        with patch.object(ocr, "_extract_ar_ap_ai_fields_routed", new=AsyncMock()) as routed:
            out = await _run_merge(primary)
    routed.assert_not_awaited()
    assert out == primary


@pytest.mark.asyncio
async def test_cross_runs_when_primary_below_skip_threshold():
    primary = _primary(confidence="0.50")
    cross = {"tsv_rows": [{"amount": "100", "confidence": "0.99"}]}
    with _cross_settings(ap_auto_cross_verify_skip_primary_confidence=0.9):
        with patch.object(
            ocr, "_extract_ar_ap_ai_fields_routed", new=AsyncMock(return_value=cross)
        ) as routed:
            out = await _run_merge(primary)
    routed.assert_awaited_once()
    assert out.get("ap_cross_vlm_audit")


@pytest.mark.asyncio
async def test_force_double_check_bypasses_skip_gate():
    primary = _primary(confidence="0.95")
    cross = {"tsv_rows": [{"amount": "100", "confidence": "0.99"}]}
    token = ocr._ap_cross_verify_force_cv.set(True)
    try:
        with _cross_settings(ap_auto_cross_verify_skip_primary_confidence=0.9):
            with patch.object(
                ocr, "_extract_ar_ap_ai_fields_routed", new=AsyncMock(return_value=cross)
            ) as routed:
                out = await _run_merge(primary)
    finally:
        ocr._ap_cross_verify_force_cv.reset(token)
    routed.assert_awaited_once()
    assert out.get("ap_cross_vlm_audit")


@pytest.mark.asyncio
async def test_cheque_orientation_popped_and_reused_as_probe():
    primary = _primary(cheque_orientation={"degrees": 270, "text": "probe text"})
    cross = {"tsv_rows": [{"amount": "100", "confidence": "0.99"}]}
    with _cross_settings():
        with patch.object(
            ocr, "_extract_ar_ap_ai_fields_routed", new=AsyncMock(return_value=cross)
        ) as routed:
            out = await _run_merge(primary)
    assert "cheque_orientation" not in out
    probe = routed.await_args.kwargs["cheque_probe"]
    assert probe["matched"] is True
    assert probe["degrees"] == 270
    assert probe["text"] == "probe text"


@pytest.mark.asyncio
async def test_cheque_orientation_popped_even_when_cross_disabled():
    primary = _primary(cheque_orientation={"degrees": 90, "text": "probe text"})
    with _cross_settings(ap_cross_vlm_model=""):
        out = await _run_merge(primary)
    assert "cheque_orientation" not in out
    assert out["tsv_rows"] == primary["tsv_rows"]


@pytest.mark.asyncio
async def test_router_probe_takes_priority_over_orientation_hint():
    primary = _primary(cheque_orientation={"degrees": 270, "text": "hint"})
    router_probe = {"matched": True, "text": "router text", "degrees": 90, "score": 100.0}
    cross = {"tsv_rows": [{"amount": "100", "confidence": "0.99"}]}
    with _cross_settings():
        with patch.object(
            ocr, "_extract_ar_ap_ai_fields_routed", new=AsyncMock(return_value=cross)
        ) as routed:
            await _run_merge(primary, cheque_probe=router_probe)
    assert routed.await_args.kwargs["cheque_probe"] is router_probe

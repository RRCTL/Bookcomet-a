"""M-VDU crop-preview gate: Detect pauses before extract (fiction geometry only)."""

from __future__ import annotations

import pytest
from PIL import Image

from app.api import ocr


@pytest.mark.asyncio
async def test_detect_pauses_for_crop_preview(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    img = tmp_path / "collage.png"
    Image.new("RGB", (400, 300), color=(40, 40, 40)).save(img)

    async def fake_detect(_path, **_kwargs):  # type: ignore[no-untyped-def]
        return [{"x": 20, "y": 30, "w": 100, "h": 80}, {"x": 200, "y": 40, "w": 90, "h": 70}]

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", fake_detect)

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(img),
        trace_id="t",
        filename="collage.png",
        ocr_provider_name="vlm",
        ocr_model_override="m",
        ocr_prompt_override=None,
        processing_mode="AP",
    )
    assert result is not None
    assert result["needs_crop_preview"] is True
    assert result["pages"] == []
    boxes = result["crop_preview"]["boxes"]
    assert len(boxes) == 2
    assert boxes[0]["index"] == 1
    assert boxes[1]["index"] == 2


@pytest.mark.asyncio
async def test_empty_detect_still_opens_gate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    img = tmp_path / "blank.png"
    Image.new("RGB", (200, 200), color=(10, 10, 10)).save(img)

    async def fake_detect(_path, **_kwargs):  # type: ignore[no-untyped-def]
        return []

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", fake_detect)

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(img),
        trace_id="t",
        filename="blank.png",
        ocr_provider_name="vlm",
        ocr_model_override="m",
        ocr_prompt_override=None,
        processing_mode="AP",
        skip_crop_preview=True,  # even skip cannot invent boxes
    )
    assert result is not None
    assert result["needs_crop_preview"] is True
    assert result["needs_split_review"] is True
    assert result["crop_preview"]["boxes"] == []
    assert "Draw boxes" in result["message"]


@pytest.mark.asyncio
async def test_confirmed_boxes_skip_detect(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    img = tmp_path / "collage.png"
    Image.new("RGB", (400, 300), color=(40, 40, 40)).save(img)

    detect_calls = {"n": 0}

    async def fake_detect(_path, **_kwargs):  # type: ignore[no-untyped-def]
        detect_calls["n"] += 1
        return [{"x": 10, "y": 10, "w": 80, "h": 60}]

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", fake_detect)

    # Short-circuit extract: crop then fail fast with empty OCR stubs via monkeypatch.
    monkeypatch.setattr(ocr, "_crop_receipt_regions", lambda _p, regions: [])

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(img),
        trace_id="t",
        filename="collage.png",
        ocr_provider_name="vlm",
        ocr_model_override="m",
        ocr_prompt_override=None,
        processing_mode="AP",
        crop_preview_confirmed=True,
        confirmed_crop_boxes=[
            {"region_norm": {"x": 0.05, "y": 0.05, "w": 0.2, "h": 0.2}},
        ],
    )
    assert detect_calls["n"] == 0
    assert result is not None
    assert result.get("needs_crop_preview") is not True
    assert result.get("pages") == []


@pytest.mark.asyncio
async def test_skip_preview_runs_detect_then_extracts(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img = tmp_path / "collage.png"
    Image.new("RGB", (400, 300), color=(40, 40, 40)).save(img)

    async def fake_detect(_path, **_kwargs):  # type: ignore[no-untyped-def]
        return [{"x": 10, "y": 10, "w": 80, "h": 60}]

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", fake_detect)
    monkeypatch.setattr(ocr, "_crop_receipt_regions", lambda _p, regions: [])

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(img),
        trace_id="t",
        filename="collage.png",
        ocr_provider_name="vlm",
        ocr_model_override="m",
        ocr_prompt_override=None,
        processing_mode="AP",
        skip_crop_preview=True,
    )
    assert result is not None
    assert result.get("needs_crop_preview") is not True


@pytest.mark.asyncio
async def test_confirmed_empty_boxes_reopens_gate(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    img = tmp_path / "collage.png"
    Image.new("RGB", (100, 100), color=(20, 20, 20)).save(img)

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(img),
        trace_id="t",
        filename="collage.png",
        ocr_provider_name="vlm",
        ocr_model_override="m",
        ocr_prompt_override=None,
        processing_mode="AP",
        crop_preview_confirmed=True,
        confirmed_crop_boxes=[],
    )
    assert result is not None
    assert result["needs_crop_preview"] is True
    assert result["needs_split_review"] is True

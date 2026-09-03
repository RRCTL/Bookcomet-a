"""Synthetic contracts for Settings VLM Detect layout (no real receipts, no network)."""

from __future__ import annotations

import pytest

from app.ocr.interfaces import OcrResult
from app.ocr.vlm_layout_detect import (
    is_vlm_detection_backend,
    parse_vlm_detect_regions,
    receipt_instance_id,
    resolve_ap_detection_backend,
    vlm_split_review_payload,
)
from app.api import ocr
from app.services.extraction_validation import attach_receipt_region_provenance


def test_backend_default_vlm(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AP_DETECTION_BACKEND", raising=False)
    assert resolve_ap_detection_backend() == "vlm"
    assert is_vlm_detection_backend() is True


def test_backend_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AP_DETECTION_BACKEND", "qwen")
    assert resolve_ap_detection_backend() == "vlm"
    assert is_vlm_detection_backend() is True
    monkeypatch.setenv("AP_DETECTION_BACKEND", "ai_layout")
    assert resolve_ap_detection_backend() == "vlm"
    monkeypatch.setenv("AP_DETECTION_BACKEND", "settings")
    assert resolve_ap_detection_backend() == "vlm"
    monkeypatch.setenv("AP_DETECTION_BACKEND", "opencv")
    assert resolve_ap_detection_backend() == "opencv"
    # AP/AR crop still uses Settings VLM even if the env label is opencv.
    assert is_vlm_detection_backend() is True


def test_vlm_valid_boxes_list() -> None:
    raw = (
        '[{"label":"receipt","bbox_2d":[100,100,400,500]},'
        '{"label":"receipt","bbox_2d":[500,120,820,480]}]'
    )
    regions = parse_vlm_detect_regions(raw, full_w=1000, full_h=1000, pad_pct=0.0)
    assert len(regions) == 2
    assert regions[0]["x"] == 100
    assert regions[0]["w"] == 300


def test_vlm_valid_boxes_objects_wrapper() -> None:
    raw = (
        '{"objects":[{"label":"receipt","bbox_2d":[50,50,200,400]},'
        '{"label":"receipt","bbox_2d":[250,50,400,400]}]}'
    )
    regions = parse_vlm_detect_regions(raw, full_w=500, full_h=500, pad_pct=0.0)
    assert len(regions) == 2


def test_vlm_valid_boxes_legacy_xywh() -> None:
    raw = (
        '{"confidence":1,"count":2,"receipts":['
        '{"x":0.1,"y":0.1,"w":0.3,"h":0.4},'
        '{"x":0.5,"y":0.1,"w":0.3,"h":0.4}]}'
    )
    regions = parse_vlm_detect_regions(raw, full_w=1000, full_h=1000, pad_pct=0.0)
    assert len(regions) == 2
    assert regions[0]["x"] == 100
    assert regions[0]["w"] == 300


def test_vlm_sample_display_xmin_unit_interval() -> None:
    raw = (
        '{"objects":['
        '{"label":"receipts","x_min":0.10,"y_min":0.20,"x_max":0.40,"y_max":0.80},'
        '{"label":"receipts","x_min":0.55,"y_min":0.15,"x_max":0.90,"y_max":0.85}'
        "]}"
    )
    regions = parse_vlm_detect_regions(raw, full_w=1000, full_h=1000, pad_pct=0.0)
    assert len(regions) == 2
    by_x = sorted(regions, key=lambda r: r["x"])
    assert by_x[0]["x"] == 100
    assert by_x[0]["y"] == 200
    assert by_x[0]["w"] == 300
    assert by_x[0]["h"] == 600
    assert by_x[1]["x"] == 550
    assert by_x[1]["w"] == 350


def test_vlm_sample_xmin_aliases_and_detect_scale() -> None:
    raw = (
        '{"objects":['
        '{"label":"receipt","xmin":100,"ymin":50,"xmax":400,"ymax":500},'
        '{"label":"receipt","x_min":500,"y_min":80,"x_max":800,"y_max":480}'
        "]}"
    )
    regions = parse_vlm_detect_regions(raw, full_w=1000, full_h=1000, pad_pct=0.0)
    assert len(regions) == 2
    assert regions[0]["x"] == 100
    assert regions[0]["w"] == 300


def test_vlm_bbox_2d_already_unit_interval() -> None:
    raw = '[{"label":"receipt","bbox_2d":[0.10,0.20,0.40,0.80]}]'
    regions = parse_vlm_detect_regions(raw, full_w=1000, full_h=1000, pad_pct=0.0)
    assert len(regions) == 1
    assert regions[0]["x"] == 100
    assert regions[0]["w"] == 300


def test_vlm_empty_response() -> None:
    assert parse_vlm_detect_regions("", full_w=100, full_h=100) == []
    assert parse_vlm_detect_regions("not json", full_w=100, full_h=100) == []
    payload = vlm_split_review_payload(
        trace_id="t",
        filename="synthetic.pdf",
        processing_mode="AP",
    )
    assert payload["needs_split_review"] is True
    assert payload["opencv_calls"] == 0
    assert payload["crop_status"] == "needs_split_review"
    assert payload["seg_source"] == "vlm_layout"


def test_vlm_duplicate_boxes() -> None:
    raw = (
        '{"objects":['
        '{"label":"receipt","bbox_2d":[100,100,400,500]},'
        '{"label":"receipt","bbox_2d":[100,100,400,500]},'
        '{"label":"receipt","bbox_2d":[102,101,399,499]}'
        "]}"
    )
    regions = parse_vlm_detect_regions(raw, full_w=1000, full_h=1000, pad_pct=0.0)
    assert len(regions) == 1


def test_vlm_invalid_coordinates_keeps_good() -> None:
    raw = (
        '{"objects":['
        '{"label":"receipt","bbox_2d":[400,100,100,200]},'
        '{"label":"receipt","bbox_2d":[10,10,200,300]}'
        "]}"
    )
    regions = parse_vlm_detect_regions(raw, full_w=1000, full_h=1000, pad_pct=0.0)
    assert len(regions) == 1
    assert regions[0]["x"] == 10


def test_vlm_fenced_json_and_trailing_comma() -> None:
    raw = '```json\n[{"label":"receipt","bbox_2d":[10,10,200,200],},]\n```'
    regions = parse_vlm_detect_regions(raw, full_w=1000, full_h=1000, pad_pct=0.0)
    assert len(regions) == 1


def test_vlm_provenance_fields() -> None:
    row: dict = {}
    attach_receipt_region_provenance(
        row,
        receipt_bbox={"x": 10, "y": 20, "w": 100, "h": 80},
        pdf_page_num=3,
        parent_image_size=(200, 200),
        segmentation_mode="vlm_detect",
        segmentation_source="vlm_layout",
        crop_status="verified_vlm_crop",
        receipt_instance_id="p3-r01",
    )
    prov = row["extraction_provenance"]
    assert prov["receipt_bbox_pixels"]["w"] == 100
    assert prov["receipt_region_norm"]["x"] == 0.05
    assert prov["segmentation_mode"] == "vlm_detect"
    assert prov["crop_status"] == "verified_vlm_crop"
    assert prov["receipt_instance_id"] == "p3-r01"


@pytest.mark.asyncio
async def test_vlm_mode_no_opencv_on_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AP_DETECTION_BACKEND", raising=False)
    opencv_calls: list[str] = []

    def boom(name: str):
        def _inner(*_a, **_k):
            opencv_calls.append(name)
            raise AssertionError(f"{name} must not run on AP/AR Settings VLM Detect")

        return _inner

    monkeypatch.setattr(ocr, "_detect_receipt_regions_v2", boom("detect_v2"))
    monkeypatch.setattr(ocr, "_force_split_receipt_regions", boom("force_split"))
    monkeypatch.setattr(ocr, "_filter_credible_receipt_regions", boom("ink_filter"))

    async def empty_layout(*_a, **kwargs):
        assert kwargs.get("vlm_only") is True
        return None

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", empty_layout)

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        "synthetic.png",
        trace_id="t",
        filename="synthetic.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="settings-vlm",
        ocr_prompt_override=None,
        processing_mode="AP",
        confirmed=False,
    )
    assert result is not None
    assert result["needs_split_review"] is True
    assert result["opencv_calls"] == 0
    assert opencv_calls == []


@pytest.mark.asyncio
async def test_vlm_mode_valid_boxes_skip_opencv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AP_DETECTION_BACKEND", raising=False)
    opencv_calls: list[str] = []

    def track(name: str):
        def _inner(*_a, **_k):
            opencv_calls.append(name)
            return []

        return _inner

    monkeypatch.setattr(ocr, "_detect_receipt_regions_v2", track("detect_v2"))
    monkeypatch.setattr(ocr, "_force_split_receipt_regions", track("force_split"))
    monkeypatch.setattr(ocr, "_filter_credible_receipt_regions", track("ink_filter"))

    async def two_boxes(*_a, **kwargs):
        assert kwargs.get("vlm_only") is True
        return [
            {"x": 10, "y": 10, "w": 80, "h": 80},
            {"x": 120, "y": 10, "w": 80, "h": 80},
        ]

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", two_boxes)
    monkeypatch.setattr(ocr, "_crop_receipt_regions", lambda *_a, **_k: [])

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        "synthetic.png",
        trace_id="t",
        filename="synthetic.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="settings-vlm",
        ocr_prompt_override=None,
        processing_mode="AP",
        confirmed=True,
        expected_receipt_count=9,
        prefer_denser_split=True,
    )
    assert result is not None
    assert result.get("needs_split_review") is not True
    assert result["count_validation"]["seg_source"] == "vlm_layout"
    assert opencv_calls == []


def test_receipt_instance_id_format() -> None:
    assert receipt_instance_id(3, 1) == "p3-r01"
    assert receipt_instance_id(12, 11) == "p12-r11"


def test_public_ap_receipt_page_keeps_crop_identity() -> None:
    page = ocr._public_ap_receipt_page(
        {
            "page": 2,
            "receipt_index": 3,
            "text": "synth",
            "lines_count": 1,
            "extracted_fields": {"amount": "1.00"},
            "field_confidence": 0.5,
            "ai_enhanced": {"tsv_rows": [{"amount": "1.00", "payee": "Synthetic"}]},
            "receipt_bbox": {"x": 10, "y": 20, "w": 30, "h": 40},
            "image_quality": {"status": "ok"},
            "crop_status": "verified_vlm_crop",
            "segmentation_mode": "vlm_detect",
            "segmentation_source": "vlm_layout",
            "status": "success",
        },
        2,
    )
    assert page["receipt_bbox"] == {"x": 10, "y": 20, "w": 30, "h": 40}
    assert page["receipt_instance_id"] == "p2-r03"
    assert page["receipt_index"] == 3
    assert page["crop_status"] == "verified_vlm_crop"
    assert page["ai_enhanced"]["tsv_rows"][0]["payee"] == "Synthetic"


def test_stub_candidate_row_links_preview() -> None:
    row = ocr._ap_stub_receipt_candidate_row(
        receipt_bbox={"x": 4, "y": 5, "w": 40, "h": 50},
        pdf_page_num=1,
        receipt_index=2,
        parent_image_size=(200, 200),
        vlm_mode=True,
    )
    prov = row["extraction_provenance"]
    assert row["needs_review"] is True
    assert "incomplete_extraction" in row["validation_flags"]
    assert prov["receipt_instance_id"] == "p1-r02"
    assert prov["receipt_bbox_pixels"] == {"x": 4, "y": 5, "w": 40, "h": 50}
    assert prov["receipt_region_norm"]["w"] == 0.2
    assert prov["segmentation_mode"] == "vlm_detect"


@pytest.mark.asyncio
async def test_vlm_each_box_becomes_instance_and_ocr(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AP_DETECTION_BACKEND", raising=False)
    monkeypatch.delenv("AP_OCR_STRUCTURED_ONLY", raising=False)
    from PIL import Image

    page_png = tmp_path / "synthetic_page.png"
    Image.new("RGB", (200, 200), (255, 255, 255)).save(page_png)

    tiny = {"x": 1, "y": 1, "w": 2, "h": 2}
    normal = {"x": 20, "y": 20, "w": 80, "h": 90}

    async def two_boxes(*_a, **kwargs):
        assert kwargs.get("vlm_only") is True
        return [tiny, normal]

    recognize_paths: list[str] = []
    extract_calls: list[str] = []
    opencv_calls: list[str] = []

    def boom(name: str):
        def _inner(*_a, **_k):
            opencv_calls.append(name)
            raise AssertionError(f"{name} must not run on AP/AR Settings VLM Detect")

        return _inner

    class _FakeOcr:
        async def recognize(self, image_path, **_k):
            recognize_paths.append(image_path)
            return OcrResult(text="SYNTHETIC", lines=[], metadata={})

    class _FakeFilter:
        def filter_and_extract(self, _result):
            return {"fields": {}, "overall_confidence": 0.9, "missing_fields": []}

    async def fake_extract(**kwargs):
        extract_calls.append(str(kwargs.get("img_path") or ""))
        return {
            "output_format": "tsv",
            "ai_processed": True,
            "tsv_rows": [{"amount": "9.00", "payee": "Synthetic Shop"}],
        }

    async def passthrough_merge(*, ai_primary, **_k):
        return ai_primary

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", two_boxes)
    monkeypatch.setattr(ocr, "_ocr_service", _FakeOcr())
    monkeypatch.setattr(ocr, "_filtering_pipeline", _FakeFilter())
    monkeypatch.setattr(ocr, "_extract_ar_ap_ai_fields_routed", fake_extract)
    monkeypatch.setattr(ocr, "_ap_apply_cross_vlm_merge_if_configured", passthrough_merge)
    monkeypatch.setattr(ocr._receipt_image_quality, "quality_enabled", lambda: False)
    monkeypatch.setattr(ocr, "_detect_receipt_regions_v2", boom("detect_v2"))
    monkeypatch.setattr(ocr, "_force_split_receipt_regions", boom("force_split"))

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(page_png),
        trace_id="t",
        filename="synthetic.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="settings-vlm",
        ocr_prompt_override=None,
        processing_mode="AP",
        confirmed=True,
        pdf_page_num=4,
    )
    assert result is not None
    pages = result["pages"]
    assert len(pages) == 2
    assert recognize_paths == []
    assert len(extract_calls) == 2
    assert opencv_calls == []
    assert {p["receipt_instance_id"] for p in pages} == {"p4-r01", "p4-r02"}
    assert pages[0]["receipt_bbox"] == tiny
    assert pages[1]["receipt_bbox"] == normal
    for page in pages:
        rows = (page.get("ai_enhanced") or {}).get("tsv_rows") or []
        assert len(rows) == 1
        prov = rows[0]["extraction_provenance"]
        assert prov["receipt_instance_id"] == page["receipt_instance_id"]
        assert prov["receipt_bbox_pixels"] == page["receipt_bbox"]
        assert "receipt_region_norm" in prov
        assert prov["segmentation_mode"] == "vlm_detect"


@pytest.mark.asyncio
async def test_structured_only_off_runs_pass1_recognize(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("AP_OCR_STRUCTURED_ONLY", "false")
    from PIL import Image

    page_png = tmp_path / "synthetic_page.png"
    Image.new("RGB", (200, 200), (255, 255, 255)).save(page_png)

    async def one_box(*_a, **_k):
        return [{"x": 20, "y": 20, "w": 80, "h": 90}]

    recognize_paths: list[str] = []

    class _FakeOcr:
        async def recognize(self, image_path, **_k):
            recognize_paths.append(image_path)
            return OcrResult(text="SYNTHETIC", lines=[], metadata={})

    class _FakeFilter:
        def filter_and_extract(self, _result):
            return {"fields": {}, "overall_confidence": 0.9, "missing_fields": []}

    async def fake_extract(**_k):
        return {
            "output_format": "tsv",
            "ai_processed": True,
            "tsv_rows": [{"amount": "1.00", "payee": "Synthetic"}],
        }

    async def passthrough_merge(*, ai_primary, **_k):
        return ai_primary

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", one_box)
    monkeypatch.setattr(ocr, "_ocr_service", _FakeOcr())
    monkeypatch.setattr(ocr, "_filtering_pipeline", _FakeFilter())
    monkeypatch.setattr(ocr, "_extract_ar_ap_ai_fields_routed", fake_extract)
    monkeypatch.setattr(ocr, "_ap_apply_cross_vlm_merge_if_configured", passthrough_merge)
    monkeypatch.setattr(ocr._receipt_image_quality, "quality_enabled", lambda: False)

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(page_png),
        trace_id="t",
        filename="synthetic.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="settings-vlm",
        ocr_prompt_override=None,
        processing_mode="AP",
        confirmed=True,
        pdf_page_num=1,
    )
    assert result is not None
    assert len(recognize_paths) == 1


@pytest.mark.asyncio
async def test_crop_extract_uses_settings_image_options(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(ocr, "AP_CROP_OCR_IMAGE_MAX_SIDE", 800)
    monkeypatch.setattr(ocr, "AP_CROP_OCR_JPEG_QUALITY", 70)
    from PIL import Image

    page_png = tmp_path / "synthetic_page.png"
    Image.new("RGB", (200, 200), (255, 255, 255)).save(page_png)

    async def one_box(*_a, **_k):
        return [{"x": 20, "y": 20, "w": 80, "h": 90}]

    seen_opts: list[dict | None] = []

    class _FakeOcr:
        async def recognize(self, *_a, **_k):
            raise AssertionError("structured-only must skip pass-1 recognize")

    class _FakeFilter:
        def filter_and_extract(self, _result):
            return {"fields": {}, "overall_confidence": 0.0, "missing_fields": []}

    async def fake_extract(**kwargs):
        seen_opts.append(kwargs.get("image_options"))
        return {
            "output_format": "tsv",
            "ai_processed": True,
            "tsv_rows": [{"amount": "2.00", "payee": "Synthetic"}],
        }

    async def passthrough_merge(*, ai_primary, **_k):
        return ai_primary

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", one_box)
    monkeypatch.setattr(ocr, "_ocr_service", _FakeOcr())
    monkeypatch.setattr(ocr, "_filtering_pipeline", _FakeFilter())
    monkeypatch.setattr(ocr, "_extract_ar_ap_ai_fields_routed", fake_extract)
    monkeypatch.setattr(ocr, "_ap_apply_cross_vlm_merge_if_configured", passthrough_merge)
    monkeypatch.setattr(ocr._receipt_image_quality, "quality_enabled", lambda: False)

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(page_png),
        trace_id="t",
        filename="synthetic.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="settings-vlm",
        ocr_prompt_override=None,
        processing_mode="AP",
        confirmed=True,
        pdf_page_num=1,
    )
    assert result is not None
    assert seen_opts == [{"max_side": 800, "format": "JPEG", "quality": 70}]


@pytest.mark.asyncio
async def test_vlm_empty_extraction_still_emits_linked_candidate(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AP_DETECTION_BACKEND", raising=False)
    from PIL import Image

    page_png = tmp_path / "synthetic_page.png"
    Image.new("RGB", (120, 120), (240, 240, 240)).save(page_png)

    async def one_box(*_a, **_k):
        return [{"x": 10, "y": 10, "w": 50, "h": 60}]

    class _FakeOcr:
        async def recognize(self, *_a, **_k):
            return OcrResult(text="", lines=[], metadata={})

    class _FakeFilter:
        def filter_and_extract(self, _result):
            return {"fields": {}, "overall_confidence": 0.0, "missing_fields": []}

    async def empty_extract(**_k):
        return {"output_format": "tsv", "ai_processed": True, "tsv_rows": []}

    async def passthrough_merge(*, ai_primary, **_k):
        return ai_primary

    monkeypatch.setattr(ocr, "_ap_vlm_layout_try_receipt_regions", one_box)
    monkeypatch.setattr(ocr, "_ocr_service", _FakeOcr())
    monkeypatch.setattr(ocr, "_filtering_pipeline", _FakeFilter())
    monkeypatch.setattr(ocr, "_extract_ar_ap_ai_fields_routed", empty_extract)
    monkeypatch.setattr(ocr, "_ap_apply_cross_vlm_merge_if_configured", passthrough_merge)
    monkeypatch.setattr(ocr._receipt_image_quality, "quality_enabled", lambda: False)

    result = await ocr._run_ap_multi_receipt_ocr_from_image(
        str(page_png),
        trace_id="t",
        filename="synthetic.pdf",
        ocr_provider_name="vlm",
        ocr_model_override="settings-vlm",
        ocr_prompt_override=None,
        processing_mode="AP",
        confirmed=True,
        pdf_page_num=1,
    )
    page = result["pages"][0]
    rows = page["ai_enhanced"]["tsv_rows"]
    assert len(rows) == 1
    assert rows[0]["needs_review"] is True
    assert rows[0]["extraction_provenance"]["receipt_instance_id"] == "p1-r01"
    assert rows[0]["extraction_provenance"]["receipt_bbox_pixels"]["w"] == 50


@pytest.mark.asyncio
async def test_detect_uses_settings_model_not_baked_id(monkeypatch, tmp_path) -> None:
    """Detect recognize() must use the caller Settings override, not a baked model id."""
    from PIL import Image

    page_png = tmp_path / "synthetic_thumb_src.png"
    Image.new("RGB", (80, 80), (250, 250, 250)).save(page_png)

    seen: dict[str, str] = {}

    class _FakeOcr:
        async def recognize(self, _path, **kwargs):
            seen["model"] = str(kwargs.get("model") or "")
            return OcrResult(
                text='[{"label":"receipt","bbox_2d":[10,10,200,200]}]',
                lines=[],
                metadata={},
            )

    monkeypatch.setattr(ocr, "_ocr_service", _FakeOcr())
    monkeypatch.setattr(
        ocr,
        "_write_ap_layout_thumbnail",
        lambda *_a, **_k: (str(page_png), 80, 80, 1000, 1000),
    )

    regions = await ocr._ap_vlm_layout_try_receipt_regions(
        str(page_png),
        ocr_provider_name="vlm",
        ocr_model_override="settings-vlm",
        vlm_only=True,
    )
    assert regions
    assert seen["model"] == "settings-vlm"
    assert "qwen" not in seen["model"].lower()


@pytest.mark.asyncio
async def test_empty_settings_model_skips_detect(monkeypatch, tmp_path) -> None:
    from PIL import Image

    page_png = tmp_path / "synthetic_thumb_src.png"
    Image.new("RGB", (40, 40), (250, 250, 250)).save(page_png)
    called = {"n": 0}

    class _FakeOcr:
        async def recognize(self, *_a, **_k):
            called["n"] += 1
            return OcrResult(text="", lines=[], metadata={})

    monkeypatch.setattr(ocr, "_ocr_service", _FakeOcr())
    monkeypatch.setattr(
        ocr,
        "_write_ap_layout_thumbnail",
        lambda *_a, **_k: (str(page_png), 40, 40, 100, 100),
    )

    regions = await ocr._ap_vlm_layout_try_receipt_regions(
        str(page_png),
        ocr_provider_name="vlm",
        ocr_model_override="",
        vlm_only=True,
    )
    assert regions is None
    assert called["n"] == 0

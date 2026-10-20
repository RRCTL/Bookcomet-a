"""Fiction-only geometry tests for M-VDU crop-preview gate."""

from __future__ import annotations

from app.services.mvdu_crop_preview import (
    build_crop_preview_payload,
    merge_neighbor_boxes,
    nudge_pixel_box,
    parse_user_crop_boxes,
    regions_to_preview_boxes,
    rotate_box_content_90,
    stamp_box_extract_status,
)


def test_regions_to_preview_boxes_numbered() -> None:
    boxes = regions_to_preview_boxes(
        [{"x": 10, "y": 20, "w": 100, "h": 80}, {"x": 200, "y": 20, "w": 90, "h": 70}],
        width=400,
        height=300,
        pdf_page=2,
    )
    assert len(boxes) == 2
    assert boxes[0]["index"] == 1
    assert boxes[1]["index"] == 2
    assert boxes[0]["page"] == 2
    assert boxes[0]["region_bbox"] == {"x": 10, "y": 20, "w": 100, "h": 80}
    assert abs(boxes[0]["region_norm"]["x"] - 10 / 400) < 1e-6
    assert boxes[0]["extract_status"] == "pending"


def test_parse_user_crop_boxes_accepts_norm_and_pixels() -> None:
    from_norm = parse_user_crop_boxes(
        [{"region_norm": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4}}],
        width=1000,
        height=1000,
    )
    assert from_norm == [{"x": 100, "y": 200, "w": 300, "h": 400}]

    from_px = parse_user_crop_boxes(
        [{"region_bbox": {"x": 5, "y": 6, "w": 50, "h": 60}, "rotation_deg": 90}],
        width=1000,
        height=1000,
    )
    assert from_px[0]["x"] == 5
    assert from_px[0]["rotation_deg"] == 90


def test_build_crop_preview_payload_empty_split_review() -> None:
    payload = build_crop_preview_payload(
        trace_id="t1",
        filename="collage.png",
        processing_mode="AP",
        pdf_page=1,
        image_width=800,
        image_height=600,
        boxes=[],
        needs_split_review=True,
        layout_failure_reason="empty_or_malformed",
    )
    assert payload["needs_crop_preview"] is True
    assert payload["needs_split_review"] is True
    assert payload["crop_preview"]["boxes"] == []
    assert "Draw boxes" in payload["message"]
    assert payload["pages"] == []


def test_build_crop_preview_payload_with_detect_boxes() -> None:
    preview = regions_to_preview_boxes(
        [{"x": 0, "y": 0, "w": 50, "h": 50}],
        width=100,
        height=100,
    )
    payload = build_crop_preview_payload(
        trace_id="t2",
        filename="page.png",
        processing_mode="AR",
        pdf_page=1,
        image_width=100,
        image_height=100,
        boxes=preview,
    )
    assert payload["needs_crop_preview"] is True
    assert payload["needs_split_review"] is False
    assert len(payload["crop_preview"]["boxes"]) == 1


def test_merge_nudge_rotate_helpers() -> None:
    merged = merge_neighbor_boxes(
        {"x": 0, "y": 0, "w": 10, "h": 10},
        {"x": 8, "y": 5, "w": 10, "h": 10},
    )
    assert merged == {"x": 0, "y": 0, "w": 18, "h": 15}
    nudged = nudge_pixel_box(
        {"x": 10, "y": 10, "w": 20, "h": 20},
        dx=-5,
        dw=5,
        width=100,
        height=100,
    )
    assert nudged["x"] == 5
    assert nudged["w"] == 25
    assert rotate_box_content_90(0) == 90
    assert rotate_box_content_90(270) == 0
    assert rotate_box_content_90(90, clockwise=False) == 0


def test_stamp_box_extract_status() -> None:
    preview = {
        "phase": "preview",
        "boxes": [
            {"id": "a", "index": 1, "extract_status": "pending"},
            {"id": "b", "index": 2, "extract_status": "pending"},
        ],
    }
    next_p = stamp_box_extract_status(preview, box_index=2, status="running", active=True)
    assert next_p is not None
    assert next_p["phase"] == "extracting"
    assert next_p["active_box_id"] == "b"
    assert next_p["boxes"][1]["extract_status"] == "running"
    assert next_p["boxes"][0]["extract_status"] == "pending"

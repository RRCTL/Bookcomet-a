"""Fiction-only geometry tests for M-VDU crop-preview gate."""

from __future__ import annotations

from app.services.mvdu_crop_preview import (
    build_crop_preview_payload,
    filter_boxes_for_page,
    merge_multipage_crop_gates,
    merge_neighbor_boxes,
    nudge_pixel_box,
    parse_user_crop_boxes,
    regions_to_preview_boxes,
    reopen_crop_gate_after_interrupt,
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


def test_reopen_crop_gate_after_interrupt_clears_extracting() -> None:
    summary = {
        "needs_crop_preview": False,
        "crop_status": "extracting",
        "crop_preview": {
            "phase": "extracting",
            "active_box_id": "a",
            "boxes": [{"id": "a", "index": 1, "extract_status": "running"}],
        },
    }
    next_s = reopen_crop_gate_after_interrupt(summary)
    assert next_s is not None
    assert next_s["needs_crop_preview"] is True
    assert next_s["crop_status"] == "needs_crop_preview"
    assert next_s["crop_preview"]["phase"] == "preview"
    assert next_s["crop_preview"]["active_box_id"] is None
    assert next_s["crop_preview"]["boxes"][0]["extract_status"] == "pending"
    assert reopen_crop_gate_after_interrupt({"crop_preview": {"phase": "done"}}) is None


def test_build_crop_preview_includes_page_entries() -> None:
    preview = regions_to_preview_boxes(
        [{"x": 0, "y": 0, "w": 40, "h": 40}],
        width=100,
        height=100,
        pdf_page=1,
    )
    payload = build_crop_preview_payload(
        trace_id="t3",
        filename="p1.png",
        processing_mode="AP",
        pdf_page=1,
        image_width=100,
        image_height=100,
        boxes=preview,
    )
    pages = payload["crop_preview"]["pages"]
    assert len(pages) == 1
    assert pages[0]["page"] == 1
    assert pages[0]["status"] == "pending"
    assert len(pages[0]["boxes"]) == 1
    # OCR outcome pages remain empty while gated.
    assert payload["pages"] == []


def test_filter_boxes_for_page() -> None:
    boxes = [
        {"page": 1, "region_norm": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}},
        {"page": 2, "region_norm": {"x": 0.3, "y": 0.3, "w": 0.2, "h": 0.2}},
        {"region_norm": {"x": 0.5, "y": 0.5, "w": 0.1, "h": 0.1}},  # legacy → page 1
    ]
    p1 = filter_boxes_for_page(boxes, pdf_page=1)
    p2 = filter_boxes_for_page(boxes, pdf_page=2)
    assert len(p1) == 2
    assert len(p2) == 1
    assert int(p2[0]["page"]) == 2


def test_merge_multipage_crop_gates() -> None:
    g1 = build_crop_preview_payload(
        trace_id="t",
        filename="doc.pdf",
        processing_mode="AP",
        pdf_page=1,
        image_width=200,
        image_height=200,
        boxes=regions_to_preview_boxes(
            [{"x": 10, "y": 10, "w": 50, "h": 50}],
            width=200,
            height=200,
            pdf_page=1,
        ),
    )
    g2 = build_crop_preview_payload(
        trace_id="t",
        filename="doc.pdf",
        processing_mode="AP",
        pdf_page=2,
        image_width=200,
        image_height=200,
        boxes=regions_to_preview_boxes(
            [{"x": 20, "y": 20, "w": 60, "h": 60}, {"x": 100, "y": 20, "w": 40, "h": 40}],
            width=200,
            height=200,
            pdf_page=2,
        ),
    )
    merged = merge_multipage_crop_gates(
        {1: g1, 2: g2},
        trace_id="t",
        filename="doc.pdf",
        processing_mode="AP",
    )
    assert merged["needs_crop_preview"] is True
    assert merged["total_pages"] == 2
    cp = merged["crop_preview"]
    assert cp["page"] == 1
    assert len(cp["boxes"]) == 1
    assert len(cp["pages"]) == 2
    assert cp["pages"][0]["page"] == 1
    assert cp["pages"][1]["page"] == 2
    assert len(cp["pages"][1]["boxes"]) == 2
    assert cp["pages"][0]["status"] == "pending"
    assert cp["pages"][1]["status"] == "pending"
    assert merged["pages"] == []

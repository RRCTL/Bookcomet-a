"""M-VDU crop-preview gate: Detect boxes before extract (Comet MultiCut pattern).

Fiction-safe helpers only — no receipt pixels. Callers supply geometry from Detect
or user edits; this module normalizes boxes and builds the pause/resume payload.
"""

from __future__ import annotations

import uuid
from typing import Any, Mapping, MutableMapping, Sequence


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def pixel_box_to_norm(
    box: Mapping[str, Any],
    *,
    width: int,
    height: int,
) -> dict[str, float] | None:
    if width < 1 or height < 1:
        return None
    try:
        x = int(box.get("x", 0))
        y = int(box.get("y", 0))
        w = int(box.get("w", 0))
        h = int(box.get("h", 0))
    except (TypeError, ValueError):
        return None
    if w <= 0 or h <= 0:
        return None
    return {
        "x": _clamp01(x / float(width)),
        "y": _clamp01(y / float(height)),
        "w": _clamp01(w / float(width)),
        "h": _clamp01(h / float(height)),
    }


def norm_box_to_pixels(
    box: Mapping[str, Any],
    *,
    width: int,
    height: int,
) -> dict[str, int] | None:
    if width < 1 or height < 1:
        return None
    x = _clamp01(_as_float(box.get("x")))
    y = _clamp01(_as_float(box.get("y")))
    w = _clamp01(_as_float(box.get("w")))
    h = _clamp01(_as_float(box.get("h")))
    if w <= 0 or h <= 0:
        return None
    rx = max(0, min(int(round(x * width)), width - 1))
    ry = max(0, min(int(round(y * height)), height - 1))
    rw = max(1, int(round(w * width)))
    rh = max(1, int(round(h * height)))
    rw = min(rw, width - rx)
    rh = min(rh, height - ry)
    if rw < 1 or rh < 1:
        return None
    return {"x": rx, "y": ry, "w": rw, "h": rh}


def new_box_id() -> str:
    return f"box_{uuid.uuid4().hex[:10]}"


def regions_to_preview_boxes(
    regions: Sequence[Mapping[str, Any]],
    *,
    width: int,
    height: int,
    pdf_page: int = 1,
) -> list[dict[str, Any]]:
    """Pixel Detect regions → numbered preview boxes (norm + pixels)."""
    out: list[dict[str, Any]] = []
    for i, reg in enumerate(regions, start=1):
        try:
            px = {
                "x": int(reg.get("x", 0)),
                "y": int(reg.get("y", 0)),
                "w": int(reg.get("w", 0)),
                "h": int(reg.get("h", 0)),
            }
        except (TypeError, ValueError):
            continue
        if px["w"] <= 0 or px["h"] <= 0:
            continue
        norm = pixel_box_to_norm(px, width=width, height=height) or {
            "x": 0.0,
            "y": 0.0,
            "w": 0.0,
            "h": 0.0,
        }
        rotation = int(reg.get("rotation_deg") or reg.get("rotation") or 0) % 360
        out.append(
            {
                "id": str(reg.get("id") or new_box_id()),
                "index": i,
                "page": int(pdf_page),
                "region_norm": norm,
                "region_bbox": px,
                "rotation_deg": rotation,
                "extract_status": "pending",
                "source": str(reg.get("source") or "vlm_detect"),
            }
        )
    return out


def parse_user_crop_boxes(
    boxes: Sequence[Mapping[str, Any]] | None,
    *,
    width: int,
    height: int,
) -> list[dict[str, int]]:
    """Accept norm and/or pixel boxes from the gate UI → pixel regions for crop."""
    if not boxes:
        return []
    regions: list[dict[str, int]] = []
    for raw in boxes:
        if not isinstance(raw, Mapping):
            continue
        rotation = int(raw.get("rotation_deg") or raw.get("rotation") or 0) % 360
        px: dict[str, int] | None = None
        region_norm = raw.get("region_norm")
        if isinstance(region_norm, Mapping):
            px = norm_box_to_pixels(region_norm, width=width, height=height)
        if px is None:
            region_bbox = raw.get("region_bbox") or raw.get("bbox")
            if isinstance(region_bbox, Mapping):
                try:
                    cand = {
                        "x": int(region_bbox.get("x", 0)),
                        "y": int(region_bbox.get("y", 0)),
                        "w": int(region_bbox.get("w", 0)),
                        "h": int(region_bbox.get("h", 0)),
                    }
                except (TypeError, ValueError):
                    cand = None
                if cand and cand["w"] > 0 and cand["h"] > 0:
                    px = cand
        if px is None and all(k in raw for k in ("x", "y", "w", "h")):
            # Prefer 0–1 if values look normalized
            if all(_as_float(raw.get(k)) <= 1.5 for k in ("x", "y", "w", "h")):
                px = norm_box_to_pixels(raw, width=width, height=height)
            else:
                try:
                    cand = {
                        "x": int(raw.get("x", 0)),
                        "y": int(raw.get("y", 0)),
                        "w": int(raw.get("w", 0)),
                        "h": int(raw.get("h", 0)),
                    }
                except (TypeError, ValueError):
                    cand = None
                if cand and cand["w"] > 0 and cand["h"] > 0:
                    px = cand
        if px is None:
            continue
        if rotation:
            px = dict(px)
            px["rotation_deg"] = rotation
        regions.append(px)
    regions.sort(key=lambda r: (r["y"] // 200, r["x"]))
    return regions


def merge_neighbor_boxes(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
) -> dict[str, Any]:
    """Union of two pixel boxes (axis-aligned)."""
    ax, ay, aw, ah = int(a["x"]), int(a["y"]), int(a["w"]), int(a["h"])
    bx, by, bw, bh = int(b["x"]), int(b["y"]), int(b["w"]), int(b["h"])
    left = min(ax, bx)
    top = min(ay, by)
    right = max(ax + aw, bx + bw)
    bottom = max(ay + ah, by + bh)
    return {"x": left, "y": top, "w": max(1, right - left), "h": max(1, bottom - top)}


def rotate_box_content_90(rotation_deg: int, *, clockwise: bool = True) -> int:
    """Track post-crop orientation (90° steps). Does not change page bbox."""
    step = 90 if clockwise else -90
    return (int(rotation_deg) + step) % 360


def nudge_pixel_box(
    box: Mapping[str, Any],
    *,
    dx: int = 0,
    dy: int = 0,
    dw: int = 0,
    dh: int = 0,
    width: int,
    height: int,
) -> dict[str, int]:
    x = max(0, int(box.get("x", 0)) + int(dx))
    y = max(0, int(box.get("y", 0)) + int(dy))
    w = max(1, int(box.get("w", 1)) + int(dw))
    h = max(1, int(box.get("h", 1)) + int(dh))
    if x + w > width:
        w = max(1, width - x)
    if y + h > height:
        h = max(1, height - y)
    x = min(x, max(0, width - 1))
    y = min(y, max(0, height - 1))
    return {"x": x, "y": y, "w": w, "h": h}


def _page_gate_entry(
    *,
    pdf_page: int,
    image_width: int,
    image_height: int,
    boxes: Sequence[Mapping[str, Any]],
    needs_split_review: bool = False,
    message: str | None = None,
    layout_failure_reason: str | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    """One page in the crop-preview gate set (page thumb + boxes)."""
    entry: dict[str, Any] = {
        "page": int(pdf_page),
        # confirm_status for the gate: pending | confirmed (RUNNING is viewer-only).
        "status": status if status in ("pending", "confirmed", "empty") else "pending",
        "image_width": int(image_width),
        "image_height": int(image_height),
        "boxes": list(boxes),
        "needs_split_review": bool(needs_split_review),
    }
    if message:
        entry["message"] = message
    if layout_failure_reason:
        entry["layout_failure_reason"] = layout_failure_reason
    return entry


def filter_boxes_for_page(
    boxes: Sequence[Mapping[str, Any]] | None,
    *,
    pdf_page: int,
) -> list[Mapping[str, Any]]:
    """Keep confirmed/Detect boxes that belong to this PDF page.

    Boxes without a page field apply only on page 1 (legacy single-page confirm).
    """
    if not boxes:
        return []
    out: list[Mapping[str, Any]] = []
    for raw in boxes:
        if not isinstance(raw, Mapping):
            continue
        try:
            box_page = int(raw.get("page") or 0)
        except (TypeError, ValueError):
            box_page = 0
        if box_page <= 0:
            if int(pdf_page) == 1:
                out.append(raw)
            continue
        if box_page == int(pdf_page):
            out.append(raw)
    return out


def normalize_skipped_pages(raw: Sequence[Any] | None) -> set[int]:
    """Parse crop-gate skipped_pages (1-based PDF page indexes)."""
    if not raw:
        return set()
    out: set[int] = set()
    for item in raw:
        try:
            page = int(item)
        except (TypeError, ValueError):
            continue
        if page >= 1:
            out.add(page)
    return out


def build_user_skipped_page_row(pdf_page: int) -> dict[str, Any]:
    """OCR page row for a crop-preview per-page Skip (no VLM extract)."""
    return {
        "page": int(pdf_page),
        "status": "skipped",
        "skipped_page": True,
        "error_code": "PAGE_SKIPPED_BY_USER",
        "error_detail": "Page skipped in crop preview — omitted from VLM extract",
        "text": "",
        "lines_count": 0,
        "extracted_fields": {},
        "field_confidence": 0.0,
        "ai_enhanced": None,
    }


def build_user_skipped_page_payload(
    *,
    trace_id: str,
    filename: str,
    processing_mode: str,
    pdf_page: int,
) -> dict[str, Any]:
    """Multi-receipt result when the whole (or this) page was Skip page'd."""
    row = build_user_skipped_page_row(pdf_page)
    return {
        "trace_id": trace_id,
        "filename": filename,
        "needs_crop_preview": False,
        "needs_split_review": False,
        "crop_status": "skipped_page",
        "message": "Page skipped in crop preview",
        "processing_mode": processing_mode,
        "seg_source": "user_skip_page",
        "opencv_calls": 0,
        "pages": [row],
        "processing_steps": {
            "seg_source": "user_skip_page",
            "opencv_calls": 0,
            "multi_receipt_split": "skipped_page",
        },
    }


def build_crop_preview_payload(
    *,
    trace_id: str,
    filename: str,
    processing_mode: str,
    pdf_page: int,
    image_width: int,
    image_height: int,
    boxes: Sequence[Mapping[str, Any]],
    needs_split_review: bool = False,
    message: str | None = None,
    layout_failure_reason: str | None = None,
    phase: str = "preview",
) -> dict[str, Any]:
    """Pause payload: full-page Detect overlay before extract (no rows written)."""
    preview_boxes = list(boxes)
    empty = len(preview_boxes) == 0
    split_review = bool(needs_split_review or empty)
    default_msg = (
        "Draw boxes or confirm forced split"
        if split_review
        else "Review Detect boxes, then confirm crops & extract"
    )
    page_entry = _page_gate_entry(
        pdf_page=pdf_page,
        image_width=image_width or 1,
        image_height=image_height or 1,
        boxes=preview_boxes,
        needs_split_review=split_review,
        message=message or default_msg,
        layout_failure_reason=layout_failure_reason,
        status="empty" if empty else "pending",
    )
    crop_preview: dict[str, Any] = {
        "phase": phase,
        "page": int(pdf_page),
        "image_width": int(image_width),
        "image_height": int(image_height),
        "boxes": preview_boxes,
        "needs_split_review": split_review,
        "active_box_id": None,
        "message": message or default_msg,
        # Page control (thumb rail). Length 1 → UI hides rail.
        "pages": [page_entry],
    }
    if layout_failure_reason:
        crop_preview["layout_failure_reason"] = layout_failure_reason

    return {
        "trace_id": trace_id,
        "filename": filename,
        "needs_crop_preview": True,
        "needs_split_review": split_review,
        "crop_status": "needs_crop_preview",
        "message": crop_preview["message"],
        "processing_mode": processing_mode,
        "seg_source": "vlm_layout",
        "opencv_calls": 0,
        "crop_preview": crop_preview,
        # OCR outcome pages stay empty while gated (distinct from crop_preview.pages).
        "pages": [],
        "processing_steps": {
            "seg_source": "vlm_layout",
            "opencv_calls": 0,
            "multi_receipt_split": "needs_crop_preview",
        },
    }


def merge_multipage_crop_gates(
    gate_by_page: Mapping[int, Mapping[str, Any]],
    *,
    trace_id: str,
    filename: str,
    processing_mode: str,
) -> dict[str, Any]:
    """Merge per-page Detect gates into one multipage crop-preview payload.

    Viewer starts on the lowest page index. Top-level OCR ``pages`` stays empty
    until extract; page thumbs live under ``crop_preview.pages``.
    """
    if not gate_by_page:
        raise ValueError("gate_by_page required")
    page_entries: list[dict[str, Any]] = []
    any_split = False
    for page_num in sorted(int(p) for p in gate_by_page.keys()):
        raw = gate_by_page[page_num]
        if not isinstance(raw, Mapping):
            continue
        cp = raw.get("crop_preview") if isinstance(raw.get("crop_preview"), Mapping) else {}
        boxes = list(cp.get("boxes") or []) if isinstance(cp, Mapping) else []
        split = bool(
            (cp.get("needs_split_review") if isinstance(cp, Mapping) else False)
            or raw.get("needs_split_review")
            or not boxes
        )
        any_split = any_split or split
        msg = None
        if isinstance(cp, Mapping) and cp.get("message") is not None:
            msg = str(cp.get("message"))
        elif raw.get("message") is not None:
            msg = str(raw.get("message"))
        failure = None
        if isinstance(cp, Mapping) and cp.get("layout_failure_reason") is not None:
            failure = str(cp.get("layout_failure_reason"))
        iw = int((cp.get("image_width") if isinstance(cp, Mapping) else None) or 1)
        ih = int((cp.get("image_height") if isinstance(cp, Mapping) else None) or 1)
        page_entries.append(
            _page_gate_entry(
                pdf_page=page_num,
                image_width=iw,
                image_height=ih,
                boxes=boxes,
                needs_split_review=split,
                message=msg,
                layout_failure_reason=failure,
                status="empty" if not boxes else "pending",
            )
        )
    if not page_entries:
        raise ValueError("no valid page gates to merge")
    active = page_entries[0]
    active_page = int(active["page"])
    active_boxes = list(active.get("boxes") or [])
    default_msg = (
        "Draw boxes or confirm forced split"
        if any_split and not active_boxes
        else "Review Detect boxes page by page, then confirm crops & extract"
    )
    crop_preview: dict[str, Any] = {
        "phase": "preview",
        "page": active_page,
        "image_width": int(active.get("image_width") or 1),
        "image_height": int(active.get("image_height") or 1),
        "boxes": active_boxes,
        "needs_split_review": bool(active.get("needs_split_review")),
        "active_box_id": None,
        "message": str(active.get("message") or default_msg),
        "pages": page_entries,
    }
    if active.get("layout_failure_reason"):
        crop_preview["layout_failure_reason"] = active["layout_failure_reason"]

    return {
        "trace_id": trace_id,
        "filename": filename,
        "needs_crop_preview": True,
        "needs_split_review": any_split,
        "crop_status": "needs_crop_preview",
        "message": crop_preview["message"],
        "processing_mode": processing_mode,
        "seg_source": "vlm_layout",
        "opencv_calls": 0,
        "crop_preview": crop_preview,
        "pages": [],
        "total_pages": len(page_entries),
        "processing_steps": {
            "seg_source": "vlm_layout",
            "opencv_calls": 0,
            "multi_receipt_split": "needs_crop_preview",
            "crop_preview_pages": len(page_entries),
        },
    }


def stamp_box_extract_status(
    crop_preview: MutableMapping[str, Any] | None,
    *,
    box_index: int,
    status: str,
    active: bool = False,
) -> dict[str, Any] | None:
    """Update strip progress during extract (pending/running/done/error)."""
    if not isinstance(crop_preview, dict):
        return None
    boxes = crop_preview.get("boxes")
    if not isinstance(boxes, list):
        return dict(crop_preview)
    next_boxes: list[Any] = []
    active_id = crop_preview.get("active_box_id")
    for b in boxes:
        if not isinstance(b, dict):
            next_boxes.append(b)
            continue
        nb = dict(b)
        try:
            idx = int(nb.get("index") or 0)
        except (TypeError, ValueError):
            idx = 0
        if idx == int(box_index):
            nb["extract_status"] = status
            if active:
                active_id = nb.get("id")
        next_boxes.append(nb)
    out = dict(crop_preview)
    out["boxes"] = next_boxes
    out["phase"] = "extracting"
    out["active_box_id"] = active_id if active else out.get("active_box_id")
    return out


def reopen_crop_gate_after_interrupt(
    summary: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Clear stale phase=extracting after Stop so the UI is not stuck mid-extract.

    Returns an updated result_summary_json (needs_crop_preview gate reopened), or
    None when there is no crop-preview payload to repair.
    """
    if not isinstance(summary, Mapping):
        return None
    cp = summary.get("crop_preview")
    if not isinstance(cp, dict):
        return None
    phase = str(cp.get("phase") or "")
    if phase not in ("extracting", "preview"):
        return None
    next_preview = dict(cp)
    next_preview["phase"] = "preview"
    next_preview["active_box_id"] = None
    boxes = next_preview.get("boxes")
    if isinstance(boxes, list):
        reset_boxes: list[Any] = []
        for b in boxes:
            if isinstance(b, dict):
                nb = dict(b)
                nb["extract_status"] = "pending"
                reset_boxes.append(nb)
            else:
                reset_boxes.append(b)
        next_preview["boxes"] = reset_boxes
    out = dict(summary)
    out["crop_preview"] = next_preview
    out["needs_crop_preview"] = True
    out["crop_status"] = "needs_crop_preview"
    out["crop_preview_dismissed"] = False
    return out


def page_image_size(image_path: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(image_path) as img:
        return int(img.size[0]), int(img.size[1])

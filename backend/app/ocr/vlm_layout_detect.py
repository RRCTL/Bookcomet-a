"""Settings VLM Detect for AP/AR layout: parse receipt boxes and hygiene only.

No OpenCV / geometry decisions live here. Boxes come from Settings → API VLM.
Used when AP_DETECTION_BACKEND is vlm (default). opencv is not used for AP/AR crop.
"""

from __future__ import annotations

import ast
import json
import os
import re
from typing import Any

# Prompt asks for JSON boxes. Parser accepts bbox_2d, x_min/xmin, and {x,y,w,h}
# in 0-1 or 0-1000. Model id is never set here — caller uses Settings VLM.
VLM_RECEIPT_DETECT_PROMPT = """Provide bounding box coordinates for every separate physical receipt in this image.
Report strictly in JSON format as a list of objects. Each object must include a
'label' and a box using one of: 'bbox_2d' [xmin, ymin, xmax, ymax],
x_min/y_min/x_max/y_max, or x/y/w/h.
Coordinates may be 0-1 (normalized) or 0-1000.
Include only complete receipt documents. Do not return background, blank cells,
logos alone, or receipt fragments.
"""

_LIST_KEYS = ("objects", "detections", "bboxes", "boxes", "results", "receipts")
_BBOX_KEYS = ("bbox_2d", "bbox", "box", "bounding_box", "xyxy")
_CORNER_KEY_SETS = (
    ("x_min", "y_min", "x_max", "y_max"),
    ("xmin", "ymin", "xmax", "ymax"),
)
_UNIT_COORD_MAX = 1.5
_MIN_NORM_SIDE = 0.005
_DETECT_SCALE = 1000.0
_DUPLICATE_IOU = 0.9

# Settings VLM Detect (no OpenCV). Legacy aliases keep an old .env working.
_VLM_BACKEND_ALIASES = frozenset(
    ("vlm", "settings", "api", "ai", "ai_layout", "qwen", "qwen_only")
)


def resolve_ap_detection_backend() -> str:
    raw = (os.getenv("AP_DETECTION_BACKEND") or "vlm").strip().lower()
    if raw in _VLM_BACKEND_ALIASES or raw == "":
        return "vlm"
    if raw == "opencv":
        return "opencv"
    return "vlm"


def is_vlm_detection_backend() -> bool:
    """AP/AR crop uses Settings VLM Detect. OpenCV is not a live AP/AR crop backend."""
    return True


def receipt_instance_id(pdf_page: int, receipt_index: int) -> str:
    """Stable id for one Detect box → native crop → OCR/VLM candidate."""
    return f"p{int(pdf_page)}-r{int(receipt_index):02d}"


def safe_parse_json(text: str) -> Any:
    """Parse a JSON list or object from VLM text (fences / trailing commas)."""
    if not text or not str(text).strip():
        return None
    cleaned = re.sub(r"```(?:json)?", "", str(text), flags=re.IGNORECASE).strip()
    match = re.search(r"(\[.*\]|\{.*\})", cleaned, re.DOTALL)
    candidates = [match.group(1)] if match else [cleaned]
    for raw in candidates:
        raw_clean = re.sub(r",\s*([}\]])", r"\1", raw)
        for loader in (json.loads, ast.literal_eval):
            try:
                return loader(raw_clean)
            except (json.JSONDecodeError, ValueError, SyntaxError, TypeError, MemoryError):
                continue
    return None


def extract_bbox_xyxy(item: dict) -> list[float] | None:
    """Read Detect arrays (bbox_2d) or corner keys (x_min / xmin)."""
    if not isinstance(item, dict):
        return None
    for key in _BBOX_KEYS:
        val = item.get(key)
        if isinstance(val, (list, tuple)) and len(val) == 4:
            try:
                return [float(val[0]), float(val[1]), float(val[2]), float(val[3])]
            except (TypeError, ValueError):
                return None
    for keys in _CORNER_KEY_SETS:
        if not all(k in item for k in keys):
            continue
        try:
            return [float(item[keys[0]]), float(item[keys[1]]), float(item[keys[2]]), float(item[keys[3]])]
        except (TypeError, ValueError):
            return None
    return None


def _coords_are_unit_interval(values: list[float]) -> bool:
    return max(abs(v) for v in values) <= _UNIT_COORD_MAX


def _xyxy_to_norm_xywh(xyxy: list[float]) -> dict[str, float] | None:
    """Map xyxy in 0-1 or 0-1000 to unit xywh."""
    if len(xyxy) != 4:
        return None
    x1, y1, x2, y2 = xyxy
    if not _coords_are_unit_interval(xyxy):
        x1, y1, x2, y2 = (
            x1 / _DETECT_SCALE,
            y1 / _DETECT_SCALE,
            x2 / _DETECT_SCALE,
            y2 / _DETECT_SCALE,
        )
    x1 = _clamp01(x1)
    y1 = _clamp01(y1)
    x2 = _clamp01(x2)
    y2 = _clamp01(y2)
    if x2 <= x1 or y2 <= y1:
        return None
    w = x2 - x1
    h = y2 - y1
    if w < _MIN_NORM_SIDE or h < _MIN_NORM_SIDE:
        return None
    return {"x": x1, "y": y1, "w": w, "h": h}


def unwrap_detection_items(parsed: Any) -> list:
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in _LIST_KEYS:
            val = parsed.get(key)
            if isinstance(val, list):
                return val
        for val in parsed.values():
            if isinstance(val, list):
                return val
    return []


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _iou(a: dict[str, float], b: dict[str, float]) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    if union <= 0:
        return 0.0
    return inter / union


def _norm_xywh_from_item(item: Any) -> dict[str, float] | None:
    """Accept bbox_2d / x_min corners, or {x,y,w,h}, in 0-1 or 0-1000."""
    if not isinstance(item, dict):
        return None
    xyxy = extract_bbox_xyxy(item)
    if xyxy is not None:
        return _xyxy_to_norm_xywh(xyxy)
    try:
        x = float(item["x"])
        y = float(item["y"])
        w = float(item["w"])
        h = float(item["h"])
    except (KeyError, TypeError, ValueError):
        return None
    if not _coords_are_unit_interval([x, y, w, h]):
        x, y, w, h = x / _DETECT_SCALE, y / _DETECT_SCALE, w / _DETECT_SCALE, h / _DETECT_SCALE
    if w <= 0 or h <= 0:
        return None
    x = _clamp01(x)
    y = _clamp01(y)
    w = min(w, 1.0 - x)
    h = min(h, 1.0 - y)
    if w < _MIN_NORM_SIDE or h < _MIN_NORM_SIDE:
        return None
    return {"x": x, "y": y, "w": w, "h": h}


def drop_duplicate_norm_boxes(boxes: list[dict[str, float]]) -> list[dict[str, float]]:
    kept: list[dict[str, float]] = []
    for box in boxes:
        if any(_iou(box, prev) >= _DUPLICATE_IOU for prev in kept):
            continue
        kept.append(box)
    return kept


def parse_vlm_detect_regions(
    raw_text: str,
    *,
    full_w: int,
    full_h: int,
    pad_pct: float = 0.02,
) -> list[dict[str, int]]:
    """Parse Detect JSON and map to native-pixel {x,y,w,h} on the source page."""
    parsed = safe_parse_json(raw_text)
    items = unwrap_detection_items(parsed)
    norms: list[dict[str, float]] = []
    for item in items:
        box = _norm_xywh_from_item(item)
        if box is not None:
            norms.append(box)
    norms = drop_duplicate_norm_boxes(norms)
    return _norm_boxes_to_pixels(norms, full_w, full_h, pad_pct)


def _norm_boxes_to_pixels(
    boxes: list[dict[str, float]],
    full_w: int,
    full_h: int,
    pad_pct: float,
) -> list[dict[str, int]]:
    if full_w < 1 or full_h < 1:
        return []
    pad = max(0.0, min(float(pad_pct), 0.45))
    regions: list[dict[str, int]] = []
    for raw in boxes:
        x, y, w, h = raw["x"], raw["y"], raw["w"], raw["h"]
        x2 = x + pad * w
        y2 = y + pad * h
        w2 = w * (1.0 - 2.0 * pad)
        h2 = h * (1.0 - 2.0 * pad)
        if w2 <= 0 or h2 <= 0:
            continue
        x2 = _clamp01(x2)
        y2 = _clamp01(y2)
        w2 = min(w2, 1.0 - x2)
        h2 = min(h2, 1.0 - y2)
        rx = max(0, min(int(x2 * full_w), full_w - 1))
        ry = max(0, min(int(y2 * full_h), full_h - 1))
        rw = max(1, int(round(w2 * full_w)))
        rh = max(1, int(round(h2 * full_h)))
        rw = min(rw, full_w - rx)
        rh = min(rh, full_h - ry)
        if rw < 1 or rh < 1:
            continue
        regions.append({"x": rx, "y": ry, "w": rw, "h": rh})
    regions.sort(key=lambda r: (r["y"] // 200, r["x"]))
    return regions


def vlm_split_review_payload(
    *,
    trace_id: str,
    filename: str,
    processing_mode: str,
    reason: str = "empty_or_malformed",
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "filename": filename,
        "needs_split_review": True,
        "crop_status": "needs_split_review",
        "message": (
            "AI layout did not return usable receipt boxes. "
            "Review the source page and draw crop regions."
        ),
        "processing_mode": processing_mode,
        "seg_source": "vlm_layout",
        "opencv_calls": 0,
        "layout_failure_reason": reason,
        "processing_steps": {
            "seg_source": "vlm_layout",
            "opencv_calls": 0,
            "multi_receipt_split": "needs_split_review",
        },
    }

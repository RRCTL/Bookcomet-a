"""On-demand receipt crop preview from stored task files (no separate crop persistence).

Uses extraction_provenance.receipt_region_norm (or full page) against the parent
upload. Returns JPEG bytes for authenticated preview only.
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def region_pixels_from_norm(
    *,
    width: int,
    height: int,
    region: Mapping[str, Any],
) -> tuple[int, int, int, int] | None:
    """Map normalized 0–1 x,y,w,h to inclusive pixel crop box (left, top, right, bottom)."""
    try:
        x = _clamp01(region.get("x", 0))
        y = _clamp01(region.get("y", 0))
        w = _clamp01(region.get("w", 0))
        h = _clamp01(region.get("h", 0))
    except Exception:
        return None
    if width < 1 or height < 1 or w <= 0 or h <= 0:
        return None
    left = int(round(x * width))
    top = int(round(y * height))
    right = int(round((x + w) * width))
    bottom = int(round((y + h) * height))
    left = max(0, min(width - 1, left))
    top = max(0, min(height - 1, top))
    right = max(left + 1, min(width, right))
    bottom = max(top + 1, min(height, bottom))
    return left, top, right, bottom


def region_pixels_from_bbox(
    *,
    width: int,
    height: int,
    bbox: Mapping[str, Any],
) -> tuple[int, int, int, int] | None:
    try:
        x = int(bbox.get("x", 0))
        y = int(bbox.get("y", 0))
        w = int(bbox.get("w", 0))
        h = int(bbox.get("h", 0))
    except Exception:
        return None
    if w <= 0 or h <= 0 or width < 1 or height < 1:
        return None
    left = max(0, min(width - 1, x))
    top = max(0, min(height - 1, y))
    right = max(left + 1, min(width, x + w))
    bottom = max(top + 1, min(height, y + h))
    return left, top, right, bottom


def _load_page_image(path: str, page: int):
    """Return PIL RGB image for an image file or a 1-indexed PDF page."""
    from PIL import Image

    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        import fitz

        doc = fitz.open(path)
        try:
            if doc.page_count < 1:
                raise ValueError("PDF has no pages")
            idx = max(0, min(doc.page_count - 1, int(page) - 1))
            pg = doc[idx]
            base_zoom = float(os.getenv("PDF_RENDER_ZOOM", "2.0"))
            pix = pg.get_pixmap(matrix=fitz.Matrix(base_zoom, base_zoom), alpha=False)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            return img
        finally:
            doc.close()

    with Image.open(path) as img:
        return img.convert("RGB")


def render_receipt_crop_jpeg(
    *,
    storage_path: str,
    page: int = 1,
    region_norm: Mapping[str, Any] | None = None,
    region_bbox: Mapping[str, Any] | None = None,
    max_side: int = 960,
    jpeg_quality: int = 82,
) -> bytes:
    """
    Render a JPEG crop for Table Review AQ preview.

    If no region is provided, returns a downscaled full page (still no separate crop store).
    """
    from PIL import Image

    path = str(storage_path)
    if not path or not Path(path).is_file():
        raise FileNotFoundError("source file missing")

    img = _load_page_image(path, page)
    box = None
    if region_norm:
        box = region_pixels_from_norm(width=img.width, height=img.height, region=region_norm)
    elif region_bbox:
        box = region_pixels_from_bbox(width=img.width, height=img.height, bbox=region_bbox)

    if box is not None:
        img = img.crop(box)

    w, h = img.size
    side = max(w, h)
    if side > max_side > 0:
        scale = max_side / float(side)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=jpeg_quality, optimize=True)
    return buf.getvalue()

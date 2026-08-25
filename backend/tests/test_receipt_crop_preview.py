"""Unit tests for on-demand receipt crop preview (synthetic drawings only)."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app.services.receipt_crop_preview import (
    region_pixels_from_bbox,
    region_pixels_from_norm,
    render_receipt_crop_jpeg,
)


def _write_synthetic_png(path: Path, *, w: int = 200, h: int = 300) -> None:
    """Abstract bars — not a photographed receipt."""
    img = Image.new("RGB", (w, h), (232, 232, 232))
    px = img.load()
    assert px is not None
    for y in range(40, 260, 20):
        for x in range(20, 180):
            px[x, y] = (80, 80, 90)
            px[x, y + 1] = (80, 80, 90)
    img.save(path, format="PNG")


def test_region_pixels_from_norm_basic() -> None:
    box = region_pixels_from_norm(width=100, height=200, region={"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.25})
    assert box == (10, 40, 60, 90)


def test_region_pixels_from_bbox_clamps() -> None:
    box = region_pixels_from_bbox(width=50, height=50, bbox={"x": -5, "y": 0, "w": 80, "h": 10})
    assert box is not None
    left, top, right, bottom = box
    assert left == 0
    assert right == 50
    assert bottom - top == 10


def test_render_receipt_crop_jpeg_full_and_region(tmp_path: Path) -> None:
    src = tmp_path / "synthetic_page.png"
    _write_synthetic_png(src)

    full = render_receipt_crop_jpeg(storage_path=str(src), page=1)
    assert full[:2] == b"\xff\xd8"  # JPEG SOI

    crop = render_receipt_crop_jpeg(
        storage_path=str(src),
        page=1,
        region_norm={"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.5},
    )
    assert crop[:2] == b"\xff\xd8"
    with Image.open(io.BytesIO(crop)) as out:
        assert out.size[0] < 200
        assert out.size[1] < 300


def test_render_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        render_receipt_crop_jpeg(storage_path=str(tmp_path / "missing.png"))

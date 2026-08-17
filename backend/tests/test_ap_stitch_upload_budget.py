"""Tests for AP multi-page vertical stitch vs VLM upload resize (collapse guard)."""

from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image


def test_stitch_collapses_on_upload_tall_document() -> None:
    from app.api.ocr import _stitch_collapses_on_upload

    with tempfile.TemporaryDirectory() as d:
        paths: list[str] = []
        for i in range(30):
            p = Path(d) / f"p{i}.png"
            Image.new("RGB", (1000, 2000), (255, 255, 255)).save(p)
            paths.append(str(p))
        assert _stitch_collapses_on_upload(paths) is True


def test_stitch_does_not_collapse_short_invoice() -> None:
    from app.api.ocr import _stitch_collapses_on_upload

    with tempfile.TemporaryDirectory() as d:
        paths: list[str] = []
        for i in range(2):
            p = Path(d) / f"p{i}.png"
            Image.new("RGB", (1000, 2000), (255, 255, 255)).save(p)
            paths.append(str(p))
        assert _stitch_collapses_on_upload(paths) is False

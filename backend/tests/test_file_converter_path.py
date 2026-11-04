"""Synthetic path checks for PDF open guards (no real receipts)."""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.utils.file_converter import (
    _assert_safe_pdf_path,
    _safe_raster_temp_suffix,
    convert_one_pdf_page_to_temp_png,
)


class SafePdfPathTest(unittest.TestCase):
    def test_temp_file_is_allowed(self) -> None:
        fd, raw = tempfile.mkstemp(prefix="bc-pdf-guard-", suffix=".bin")
        os.close(fd)
        try:
            resolved = _assert_safe_pdf_path(raw)
            self.assertEqual(resolved, Path(os.path.realpath(raw)))
        finally:
            os.unlink(raw)

    def test_parent_segments_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _assert_safe_pdf_path("../not-a-receipt.pdf")

    def test_file_outside_uploads_and_temp_is_rejected(self) -> None:
        tmp = Path(tempfile.gettempdir()).resolve()
        root = Path(__file__).resolve().parent / f".bc-pdf-outside-{uuid.uuid4().hex}"
        if root.resolve().is_relative_to(tmp):
            self.skipTest("test file would sit inside tempdir")
        root.mkdir(parents=True, exist_ok=True)
        outside = root / "escape.bin"
        outside.write_bytes(b"x")
        prev = os.environ.get("UPLOADS_DIR")
        uploads = Path(tempfile.mkdtemp(prefix="bc-uploads-"))
        try:
            os.environ["UPLOADS_DIR"] = str(uploads)
            with self.assertRaises(ValueError):
                _assert_safe_pdf_path(str(outside))
        finally:
            if prev is None:
                os.environ.pop("UPLOADS_DIR", None)
            else:
                os.environ["UPLOADS_DIR"] = prev
            outside.unlink(missing_ok=True)
            root.rmdir()
            uploads.rmdir()


class SafeRasterTempSuffixTest(unittest.TestCase):
    def test_allowlisted_constants_only(self) -> None:
        self.assertEqual(_safe_raster_temp_suffix("PNG"), ".png")
        self.assertEqual(_safe_raster_temp_suffix("JPEG"), ".jpg")
        self.assertEqual(_safe_raster_temp_suffix("JPG"), ".jpg")
        self.assertEqual(_safe_raster_temp_suffix("weird"), ".png")
        # Returned values must be exact allowlisted constants (no separators).
        for fmt in ("PNG", "JPEG", "JPG", "", "x./../y"):
            suffix = _safe_raster_temp_suffix(fmt)
            self.assertIn(suffix, (".png", ".jpg"))
            self.assertNotIn("..", suffix)
            self.assertNotIn("/", suffix)
            self.assertNotIn("\\", suffix)

    def test_convert_one_page_temp_name_excludes_page_number(self) -> None:
        """CodeQL py/path-injection: page must not appear in tempfile suffix."""
        from PIL import Image as PilImage

        fd, pdf_raw = tempfile.mkstemp(prefix="bc-fictional-pdf-", suffix=".bin")
        os.close(fd)
        buf = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        try:
            PilImage.new("RGB", (2, 2), color=(10, 20, 30)).save(buf, format="PNG")
            buf.close()
            png_bytes = Path(buf.name).read_bytes()

            page = MagicMock()
            page.rect.width = 100.0
            page.rect.height = 100.0
            pix = MagicMock()
            pix.width = 2
            pix.height = 2
            pix.tobytes.return_value = png_bytes

            doc = MagicMock()
            doc.page_count = 3
            doc.__getitem__.return_value = page

            with (
                patch("app.utils.file_converter._resolved_pdf_path", return_value=pdf_raw),
                patch("fitz.open", return_value=doc),
                patch(
                    "app.utils.file_converter._page_pixmap_within_budget",
                    return_value=(pix, 1.0),
                ),
            ):
                out = convert_one_pdf_page_to_temp_png(pdf_raw, 2)
            try:
                name = Path(out).name
                self.assertTrue(name.endswith(".png"))
                self.assertNotIn("_page2", name)
                self.assertNotIn("page2", name.lower())
            finally:
                os.unlink(out)
        finally:
            os.unlink(buf.name)
            os.unlink(pdf_raw)


if __name__ == "__main__":
    unittest.main()

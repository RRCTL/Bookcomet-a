"""Synthetic path checks for PDF open guards (no real receipts)."""
from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path

from app.utils.file_converter import _assert_safe_pdf_path


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


if __name__ == "__main__":
    unittest.main()

"""Tests for CodeQL py/path-injection alert #14: tempfile suffix + path containment."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from app.services.file_storage import (
    ALLOWED_UPLOAD_EXTENSIONS,
    resolve_path_under_root,
    safe_upload_temp_suffix,
)
from app.services.pool2_storage import Pool2Storage


@pytest.mark.parametrize(
    "filename,expected",
    (
        ("invoice.pdf", ".pdf"),
        ("receipt.JPG", ".jpg"),
        ("statement.XLSX", ".xlsx"),
        ("note.csv", ".csv"),
        ("scan.TIFF", ".tiff"),
        (r"C:\uploads\bank.png", ".png"),
        ("/var/tmp/doc.webp", ".webp"),
    ),
)
def test_safe_upload_temp_suffix_accepts_normal_names(filename: str, expected: str) -> None:
    assert safe_upload_temp_suffix(filename) == expected
    assert safe_upload_temp_suffix(filename) in ALLOWED_UPLOAD_EXTENSIONS


@pytest.mark.parametrize(
    "filename",
    (
        "invoice./../../etc/passwd",
        "../etc/passwd",
        "/etc/passwd",
        "..\\..\\windows\\system32\\config",
        "file.pdf/../../../etc/shadow",
        "no-extension",
        "evil.exe",
        "archive.tar.gz",  # .gz not allowlisted
        "",
        None,
    ),
)
def test_safe_upload_temp_suffix_rejects_traversal_and_unknown(filename: str | None) -> None:
    with pytest.raises(ValueError, match="not allowed|missing"):
        safe_upload_temp_suffix(filename)


def test_safe_upload_temp_suffix_nested_name_uses_basename_only() -> None:
    # Path noise before the basename must not affect the allowlisted constant returned.
    assert safe_upload_temp_suffix("x./../y.pdf") == ".pdf"
    assert safe_upload_temp_suffix("foo%2e%2e%2fbar.pdf") == ".pdf"


def test_safe_upload_temp_suffix_blocks_splitext_separator_attack() -> None:
    """Names where pathlib sees no safe suffix must not reach tempfile."""
    sneaky = "invoice./../../etc/passwd"
    assert Path(sneaky).suffix == ""
    with pytest.raises(ValueError):
        safe_upload_temp_suffix(sneaky)


def test_named_temporary_file_only_gets_allowlisted_suffix(tmp_path: Path) -> None:
    suffix = safe_upload_temp_suffix("bank-statement.pdf")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=tmp_path, mode="wb") as tmp:
        tmp.write(b"%PDF-1.4\n")
        name = tmp.name
    try:
        assert name.endswith(".pdf")
        assert ".." not in Path(name).name
        assert Path(name).resolve().is_relative_to(tmp_path.resolve())
    finally:
        os.unlink(name)


def test_pool2_load_rejects_path_escape(tmp_path: Path) -> None:
    store = Pool2Storage(root=tmp_path)
    cid, path = store.save_node_output("co", "run", "node", {"ok": True})
    assert store.load_node_output(path) == {"ok": True}

    outside = tmp_path.parent / "secret-pool2.json"
    outside.write_text('{"leak": true}', encoding="utf-8")
    try:
        assert store.load_node_output(str(outside)) is None
        assert store.load_node_output(str(tmp_path / ".." / outside.name)) is None
    finally:
        outside.unlink(missing_ok=True)


def test_resolve_path_rejects_absolute_and_parent(tmp_path: Path) -> None:
    root = tmp_path / "uploads"
    root.mkdir()
    good = root / "co" / "a.pdf"
    good.parent.mkdir(parents=True)
    good.write_bytes(b"%PDF-1.4\n")
    assert resolve_path_under_root(root, good) == good.resolve()

    with pytest.raises(ValueError):
        resolve_path_under_root(root, tmp_path / "outside.pdf")
    with pytest.raises(ValueError):
        resolve_path_under_root(root, root / ".." / "outside.pdf")

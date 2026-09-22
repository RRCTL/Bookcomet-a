import os
from pathlib import Path

from app.services.file_storage import LocalDiskStorage, write_bytes_atomic


def test_write_bytes_atomic_creates_file(tmp_path):
    dest = tmp_path / "company" / "task" / "abc.pdf"
    write_bytes_atomic(dest, b"hello")
    assert dest.read_bytes() == b"hello"


def test_local_disk_storage_save(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    store = LocalDiskStorage(str(tmp_path))
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    path = store.save("co1", "task1", "fid", pdf, ".pdf")
    assert Path(path).exists()
    assert Path(path).read_bytes() == pdf


def test_save_job_input(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    store = LocalDiskStorage(str(tmp_path))
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    path = store.save_job_input("job-1", png, ".png")
    assert os.path.isfile(path)
    assert path.endswith("input.png")


def test_optional_upload_encryption_roundtrip(tmp_path, monkeypatch):
    from app.services.file_storage import read_stored_bytes

    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    monkeypatch.setenv("UPLOADS_ENCRYPTION_KEY", "example-local-upload-key")
    store = LocalDiskStorage(str(tmp_path))
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    path = store.save("co1", "task1", "enc1", pdf, ".pdf")
    on_disk = Path(path).read_bytes()
    assert on_disk.startswith(b"BCENC1\n")
    assert pdf not in on_disk
    assert read_stored_bytes(path) == pdf


def test_load_and_delete_reject_path_escape(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    store = LocalDiskStorage(str(tmp_path))
    outside = tmp_path.parent / "secret.txt"
    outside.write_text("nope")
    try:
        store.load_path(str(outside))
        raise AssertionError("expected path escape to fail")
    except ValueError as exc:
        assert "escapes" in str(exc)
    store.delete(str(outside))
    assert outside.exists()


def test_read_stored_bytes_rejects_outside_uploads(tmp_path, monkeypatch):
    from app.services.file_storage import read_stored_bytes

    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    outside = tmp_path.parent / "outside-upload.bin"
    outside.write_bytes(b"secret")
    try:
        read_stored_bytes(outside)
        raise AssertionError("expected path escape to fail")
    except ValueError as exc:
        assert "escapes" in str(exc)


def test_write_bytes_atomic_rejects_parent_segments(tmp_path):
    dest = tmp_path / "ok" / ".." / "escape.bin"
    try:
        write_bytes_atomic(dest, b"nope")
        raise AssertionError("expected parent segments to fail")
    except ValueError as exc:
        assert "Invalid destination path" in str(exc)


def test_resolve_path_under_root_rejects_absolute_outside(tmp_path):
    from app.services.file_storage import resolve_path_under_root

    outside = tmp_path.parent / "abs-escape.bin"
    outside.write_bytes(b"secret")
    try:
        resolve_path_under_root(tmp_path, outside)
        raise AssertionError("expected absolute path outside root to fail")
    except ValueError as exc:
        assert "escapes" in str(exc)

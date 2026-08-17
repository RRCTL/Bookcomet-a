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

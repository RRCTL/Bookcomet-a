"""
File storage adapter.

Provides a thin abstraction over local disk storage so the upload/download
logic in tasks.py never has to care about where files are stored.

Swap FILE_STORAGE_BACKEND=s3 in .env and implement S3Storage to migrate
to cloud object storage with zero changes to the API routes.

Local disk path structure (enforces tenant isolation at the filesystem level):
  {UPLOADS_DIR}/{company_id}/{task_id}/{uuid}{ext}

The original filename is NEVER used as the on-disk path to prevent path
traversal attacks. It is stored in the task_files.original_filename column.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_SAFE_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_path_segment(value: str, *, field_name: str) -> str:
    cleaned = (value or "").strip()
    if (
        not cleaned
        or ".." in cleaned
        or "/" in cleaned
        or "\\" in cleaned
        or not _SAFE_SEGMENT_RE.match(cleaned)
    ):
        raise ValueError(f"Invalid {field_name} for storage path")
    return cleaned


def assert_upload_size(data: bytes, *, max_upload_size_mb: int | None = None) -> None:
    """Raise ValueError when payload exceeds MAX_UPLOAD_SIZE_MB."""
    limit_mb = max_upload_size_mb
    if limit_mb is None:
        raw = (os.getenv("MAX_UPLOAD_SIZE_MB", "50") or "50").strip()
        try:
            limit_mb = int(raw)
        except ValueError:
            limit_mb = 50
    limit_bytes = max(1, int(limit_mb)) * 1024 * 1024
    if len(data) > limit_bytes:
        raise ValueError(
            f"Upload exceeds maximum size of {limit_mb} MB ({len(data)} bytes)"
        )


# SEC-CODE-004: whitelist of user-upload types (OCR / bank / spreadsheet).
ALLOWED_UPLOAD_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".tif",
    ".tiff",
    ".bmp",
    ".webp",
    ".csv",
    ".xlsx",
    ".xls",
}

_MAGIC_PREFIXES: list[tuple[bytes, frozenset[str]]] = [
    (b"%PDF", frozenset({".pdf"})),
    (b"\xff\xd8\xff", frozenset({".jpg", ".jpeg"})),
    (b"\x89PNG\r\n\x1a\n", frozenset({".png"})),
    (b"II*\x00", frozenset({".tif", ".tiff"})),
    (b"MM\x00*", frozenset({".tif", ".tiff"})),
    (b"BM", frozenset({".bmp"})),
    (b"PK\x03\x04", frozenset({".xlsx"})),  # OOXML zip
    (b"\xd0\xcf\x11\xe0", frozenset({".xls"})),  # OLE compound
]


def assert_file_type(filename: str, data: bytes) -> None:
    """Validate extension + magic bytes for uploaded document bytes."""
    ext = Path(filename or "").suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(
            f"File type {ext or '(none)'} not allowed. "
            f"Supported: {', '.join(sorted(ALLOWED_UPLOAD_EXTENSIONS))}"
        )
    if not data:
        raise ValueError("Uploaded file is empty")

    # CSV: no reliable magic; reject NUL bytes (likely binary malware).
    if ext == ".csv":
        if b"\x00" in data[:4096]:
            raise ValueError("CSV upload looks binary; rejected")
        return

    # WebP: RIFF....WEBP
    if ext == ".webp":
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return
        raise ValueError("File signature mismatch for .webp")

    for magic, allowed_exts in _MAGIC_PREFIXES:
        if data.startswith(magic):
            if ext not in allowed_exts:
                raise ValueError(
                    f"File signature mismatch: extension {ext} does not match content"
                )
            return

    raise ValueError(f"Unknown or invalid file format for {ext}")


def write_bytes_atomic(dest: Path, data: bytes) -> None:
    """Write bytes via temp file + os.replace to avoid partial reads."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dest.parent, prefix=".tmp_", suffix=dest.suffix)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp_path, dest)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


@runtime_checkable
class FileStorageBackend(Protocol):
    def save(
        self,
        company_id: str,
        task_id: str,
        file_uuid: str,
        data: bytes,
        ext: str,
    ) -> str:
        """Persist file bytes and return the storage path (opaque to the client)."""
        ...

    def load_path(self, storage_path: str) -> Path:
        """Return the filesystem Path for streaming a download."""
        ...

    def delete(self, storage_path: str) -> None:
        """Remove file bytes. Missing file is silently ignored."""
        ...


class LocalDiskStorage:
    """
    Stores files under {uploads_dir}/{company_id}/{task_id}/{uuid}{ext}.

    company_id in the path = tenant isolation at the filesystem level.
    """

    def __init__(self, uploads_dir: str | None = None) -> None:
        self._root = Path(uploads_dir or os.getenv("UPLOADS_DIR", "./uploads"))

    def save(
        self,
        company_id: str,
        task_id: str,
        file_uuid: str,
        data: bytes,
        ext: str,
    ) -> str:
        company_id = _safe_path_segment(company_id, field_name="company_id")
        task_id = _safe_path_segment(task_id, field_name="task_id")
        file_uuid = _safe_path_segment(file_uuid, field_name="file_uuid")
        assert_upload_size(data)
        norm_ext = (ext or "").lower()
        if not norm_ext.startswith("."):
            norm_ext = f".{norm_ext}" if norm_ext else ""
        assert_file_type(f"{file_uuid}{norm_ext}", data)
        dest = self._root / company_id / task_id / f"{file_uuid}{norm_ext}"
        # Ensure resolved path stays under uploads root (defense in depth).
        resolved = dest.resolve()
        root_resolved = self._root.resolve()
        if not resolved.is_relative_to(root_resolved):
            raise ValueError("Storage path escapes uploads directory")
        write_bytes_atomic(dest, data)
        logger.debug("[FileStorage] Saved %d bytes → %s", len(data), dest)
        return str(dest)

    def save_job_input(self, job_id: str, data: bytes, ext: str) -> str:
        """Background OCR job upload: uploads/background_jobs/{job_id}/input{ext}."""
        job_id = _safe_path_segment(job_id, field_name="job_id")
        assert_upload_size(data)
        norm_ext = (ext or "").lower()
        if not norm_ext.startswith("."):
            norm_ext = f".{norm_ext}" if norm_ext else ""
        assert_file_type(f"input{norm_ext}", data)
        dest = self._root / "background_jobs" / job_id / f"input{norm_ext}"
        write_bytes_atomic(dest, data)
        return str(dest)

    def load_path(self, storage_path: str) -> Path:
        return Path(storage_path)

    def delete(self, storage_path: str) -> None:
        try:
            Path(storage_path).unlink(missing_ok=True)
            logger.debug("[FileStorage] Deleted %s", storage_path)
        except Exception as exc:
            logger.warning("[FileStorage] Could not delete %s: %s", storage_path, exc)


# ── Module-level singleton ────────────────────────────────────────────────────
# tasks.py imports this directly. Swap the implementation here for S3.

_backend_name = os.getenv("FILE_STORAGE_BACKEND", "local")

if _backend_name == "local":
    storage: FileStorageBackend = LocalDiskStorage()
else:
    raise NotImplementedError(
        f"FILE_STORAGE_BACKEND={_backend_name!r} is not implemented. "
        "Set FILE_STORAGE_BACKEND=local or implement S3Storage."
    )

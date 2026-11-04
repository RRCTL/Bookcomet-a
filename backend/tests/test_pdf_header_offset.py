"""Regression: PDFs with %PDF- offset within first 1024 bytes (scanner/bank prefix)."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import fitz
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api import workflows as workflows_api
from app.core.security import create_access_token
from app.database import Base, get_db
from app.models.identity import Company, Membership, User
from app.services.file_storage import (
    assert_file_type,
    normalize_pdf_bytes,
    pdf_header_offset,
    prepare_upload_bytes,
    LocalDiskStorage,
)
from app.utils.file_converter import pdf_document_page_count, convert_one_pdf_page_to_temp_png


def _minimal_pdf_bytes(*, text: str = "Synthetic page") -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((40, 100), text)
    raw = doc.tobytes()
    doc.close()
    assert raw.startswith(b"%PDF-")
    return raw


def _prefixed_pdf(prefix: bytes, pdf: bytes | None = None) -> bytes:
    body = pdf if pdf is not None else _minimal_pdf_bytes()
    return prefix + body


FICTIONAL_PREFIX = b"0000000000-00-0000-0" + b" " * (64 - len(b"0000000000-00-0000-0"))


def test_pdf_header_offset_at_zero():
    pdf = _minimal_pdf_bytes()
    assert pdf_header_offset(pdf) == 0


def test_pdf_header_offset_after_64_byte_prefix():
    data = _prefixed_pdf(FICTIONAL_PREFIX)
    assert pdf_header_offset(data) == 64
    assert data[64:69] == b"%PDF-"


def test_pdf_header_offset_none_when_beyond_1024():
    pdf = _minimal_pdf_bytes()
    data = (b"X" * 1025) + pdf
    assert pdf_header_offset(data) is None


def test_assert_file_type_accepts_prefixed_pdf():
    data = _prefixed_pdf(FICTIONAL_PREFIX)
    assert_file_type("statement.pdf", data)


def test_assert_file_type_rejects_header_beyond_1024():
    pdf = _minimal_pdf_bytes()
    data = (b"X" * 1025) + pdf
    with pytest.raises(ValueError, match="no %PDF- header within the first 1024 bytes"):
        assert_file_type("statement.pdf", data)


def test_assert_file_type_rejects_non_pdf_with_pdf_extension():
    with pytest.raises(ValueError, match="no %PDF- header"):
        assert_file_type("fake.pdf", b"PK\x03\x04not-a-pdf-zip-payload")


def test_assert_file_type_does_not_loosen_png():
    data = _prefixed_pdf(FICTIONAL_PREFIX)
    with pytest.raises(ValueError, match="Unknown or invalid file format for .png"):
        assert_file_type("photo.png", data)


def test_prepare_upload_bytes_strips_leading_junk():
    pdf = _minimal_pdf_bytes()
    data = _prefixed_pdf(FICTIONAL_PREFIX, pdf)
    out = prepare_upload_bytes("doc.pdf", data)
    assert out.startswith(b"%PDF-")
    assert out == pdf
    assert normalize_pdf_bytes(data) == pdf


def test_storage_save_normalizes_prefixed_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    store = LocalDiskStorage(str(tmp_path))
    pdf = _minimal_pdf_bytes()
    data = _prefixed_pdf(FICTIONAL_PREFIX, pdf)
    path = store.save("co1", "task1", "fid1", data, ".pdf")
    stored = Path(path).read_bytes()
    assert stored.startswith(b"%PDF-")
    assert stored == pdf


def test_pymupdf_renders_normalized_prefixed_pdf(tmp_path):
    """Downstream: page count + rasterize after normalize (xref offsets intact)."""
    pdf = _minimal_pdf_bytes(text="Hello offset")
    data = _prefixed_pdf(FICTIONAL_PREFIX, pdf)
    normalized = normalize_pdf_bytes(data)
    dest = tmp_path / "normalized.pdf"
    dest.write_bytes(normalized)

    assert pdf_document_page_count(str(dest)) == 1
    png_path = convert_one_pdf_page_to_temp_png(str(dest), 1)
    try:
        assert os.path.isfile(png_path)
        assert os.path.getsize(png_path) > 0
        doc = fitz.open(str(dest))
        try:
            assert "Hello offset" in doc[0].get_text()
        finally:
            doc.close()
    finally:
        os.unlink(png_path)


def _workflow_client(tmp_path: Path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    user_id = str(uuid.uuid4())
    company_id = str(uuid.uuid4())
    email = "pdf-offset@example.com"
    db.add(
        User(
            id=user_id,
            username="pdf_offset_user",
            email=email,
            display_name="PDF Offset",
            is_active=True,
            is_verified=True,
            session_version=0,
        )
    )
    db.add(Company(id=company_id, name="PDF Offset Co"))
    db.add(
        Membership(
            id=str(uuid.uuid4()),
            user_id=user_id,
            company_id=company_id,
            role="owner",
        )
    )
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(workflows_api.router)

    def override_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_db
    os.environ["UPLOADS_DIR"] = str(tmp_path)
    client = TestClient(app)
    token = create_access_token(user_id, email, session_version=0)
    headers = {"Authorization": f"Bearer {token}", "X-Company-ID": company_id}
    return client, headers, tmp_path


def test_workflow_run_upload_accepts_64_byte_prefixed_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    store = LocalDiskStorage(str(tmp_path))
    monkeypatch.setattr(workflows_api, "storage", store)
    client, headers, uploads = _workflow_client(tmp_path)

    created = client.post(
        "/api/workflows/runs",
        headers=headers,
        json={"processing_mode": "AP"},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]

    pdf = _minimal_pdf_bytes(text="Workflow upload")
    payload = _prefixed_pdf(FICTIONAL_PREFIX, pdf)
    assert not payload.startswith(b"%PDF-")
    assert payload[64:69] == b"%PDF-"

    resp = client.post(
        f"/api/workflows/runs/{run_id}/files",
        headers=headers,
        files={"file": ("fictional-statement.pdf", payload, "application/pdf")},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["page_count"] == 1
    assert body["file_size_bytes"] == len(pdf)

    # Stored file must start at %PDF- and render.
    stored = list(uploads.rglob("*.pdf"))
    assert len(stored) == 1
    stored_bytes = stored[0].read_bytes()
    assert stored_bytes.startswith(b"%PDF-")
    assert pdf_document_page_count(str(stored[0])) == 1
    doc = fitz.open(str(stored[0]))
    try:
        assert "Workflow upload" in doc[0].get_text()
    finally:
        doc.close()


def test_workflow_run_upload_rejects_header_beyond_1024(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(workflows_api, "storage", LocalDiskStorage(str(tmp_path)))
    client, headers, _uploads = _workflow_client(tmp_path)
    created = client.post(
        "/api/workflows/runs",
        headers=headers,
        json={"processing_mode": "AP"},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]

    pdf = _minimal_pdf_bytes()
    payload = (b"Y" * 1025) + pdf
    resp = client.post(
        f"/api/workflows/runs/{run_id}/files",
        headers=headers,
        files={"file": ("too-far.pdf", payload, "application/pdf")},
    )
    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert "1024" in detail
    assert "%PDF-" in detail


def test_workflow_run_upload_rejects_renamed_non_pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path))
    monkeypatch.setattr(workflows_api, "storage", LocalDiskStorage(str(tmp_path)))
    client, headers, _uploads = _workflow_client(tmp_path)
    created = client.post(
        "/api/workflows/runs",
        headers=headers,
        json={"processing_mode": "AP"},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["id"]

    resp = client.post(
        f"/api/workflows/runs/{run_id}/files",
        headers=headers,
        files={"file": ("not-really.pdf", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "application/pdf")},
    )
    assert resp.status_code == 400, resp.text
    assert "no %PDF- header" in resp.json()["detail"]

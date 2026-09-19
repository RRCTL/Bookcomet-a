"""SEC-CODE-014..016: CodeQL path, email ReDoS, and secret-logging fixes."""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.api.identity import is_valid_email
from app.utils.file_converter import _resolved_pdf_path


@pytest.mark.parametrize(
    "email",
    (
        "user@example.com",
        "a.b+tag@sub.example.co.uk",
        "bookcomet-test@example.com",
    ),
)
def test_is_valid_email_accepts_simple_addresses(email: str) -> None:
    assert is_valid_email(email) is True


@pytest.mark.parametrize(
    "email",
    (
        "",
        "no-at-sign",
        "a@" + ("b" * 300) + ".com",
        "user@example",
        "user@.example.com",
        "user@example.com.",
        "user@@example.com",
        "user name@example.com",
        "user@exam ple.com",
    ),
)
def test_is_valid_email_rejects_malformed(email: str) -> None:
    assert is_valid_email(email) is False


def test_resolved_pdf_path_allows_temp_file(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    assert Path(_resolved_pdf_path(str(pdf))) == pdf.resolve()


def test_resolved_pdf_path_rejects_parent_segments(tmp_path: Path) -> None:
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    sneaky = str(tmp_path / ".." / pdf.name)
    with pytest.raises(ValueError, match="parent segments"):
        _resolved_pdf_path(sneaky)


def test_resolved_pdf_path_rejects_outside_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    other_temp = tmp_path / "other-temp"
    other_temp.mkdir()
    monkeypatch.setenv("UPLOADS_DIR", str(uploads))
    monkeypatch.setattr("app.utils.file_converter.tempfile.gettempdir", lambda: str(other_temp))
    outside = tmp_path / "not-allowed.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    with pytest.raises(ValueError, match="allowed directories"):
        _resolved_pdf_path(str(outside))


def test_ai_enhance_client_requires_https(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_ENHANCE_API_KEY", "sk-test-key")
    monkeypatch.setenv("AI_ENHANCE_BASE_URL", "http://example.com")
    monkeypatch.setenv("AI_ENHANCE_MODEL", "test-model")
    from app.services.ai_enhance_client import AiEnhanceClient

    with pytest.raises(ValueError, match="HTTPS"):
        AiEnhanceClient()


def test_ai_enhance_client_does_not_log_api_key(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("AI_ENHANCE_API_KEY", "sk-super-secret-key-value")
    monkeypatch.setenv("AI_ENHANCE_BASE_URL", "https://example.com")
    monkeypatch.setenv("AI_ENHANCE_MODEL", "test-model")
    from app.services.ai_enhance_client import AiEnhanceClient

    with caplog.at_level(logging.INFO, logger="app.services.ai_enhance_client"):
        AiEnhanceClient()
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "sk-super-secret-key-value" not in joined
    assert "key-value" not in joined
    assert "API key redacted" in joined

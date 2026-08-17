import pytest
from unittest.mock import AsyncMock, patch

from app.api.workflows import _upload_page_count


@pytest.mark.asyncio
async def test_upload_page_count_non_pdf_returns_one():
    assert await _upload_page_count("/tmp/file.jpg", ".jpg", "image/jpeg") == 1


@pytest.mark.asyncio
async def test_upload_page_count_pdf_uses_pymupdf():
    with patch(
        "app.utils.file_converter.pdf_document_page_count",
        return_value=8,
    ):
        assert await _upload_page_count("/tmp/doc.pdf", ".pdf", "application/pdf") == 8


@pytest.mark.asyncio
async def test_upload_page_count_pdf_failure_falls_back_to_one():
    with patch(
        "app.utils.file_converter.pdf_document_page_count",
        side_effect=Exception("corrupt"),
    ):
        assert await _upload_page_count("/tmp/bad.pdf", ".pdf", "application/pdf") == 1

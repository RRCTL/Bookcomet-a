"""P0 BANK performance / zero-fabrication safe-stop — synthetic only."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.bank_parse_outcome import (
    STATUS_ABSTAINED_NEEDS_LAYOUT,
    STATUS_BANK_SELECTION_REQUIRED,
    fallback_allowed_for_status,
)
from app.services.bank_statement_parser import BankStatementParser
from app.services.ocr_service import OcrService
from app.ocr.interfaces import OcrResult


def test_fallback_blocked_for_abstain_and_selection():
    assert fallback_allowed_for_status(STATUS_ABSTAINED_NEEDS_LAYOUT) is False
    assert fallback_allowed_for_status(STATUS_BANK_SELECTION_REQUIRED) is False
    assert fallback_allowed_for_status("completed") is True


@pytest.mark.asyncio
async def test_dispatcher_bank_hint_skips_second_bank_id(monkeypatch):
    """Parser must not re-run image bank-ID when dispatcher already attempted."""
    parser = BankStatementParser.__new__(BankStatementParser)
    id_calls = {"n": 0}

    async def _id(_path):
        id_calls["n"] += 1
        return "UNKNOWN"

    monkeypatch.setattr(parser, "_identify_bank_from_image", _id)
    monkeypatch.setattr(parser, "_detect_bank", lambda _t: "UNKNOWN")
    monkeypatch.setattr(parser, "_emit_progress", lambda *a, **k: None)

    class _Doc:
        def __len__(self):
            return 2

        def __iter__(self):
            return iter([])

    class _Page:
        def get_text(self):
            return ""

    def _open(_path):
        d = _Doc()
        d.__iter__ = lambda self: iter([_Page(), _Page()])  # type: ignore
        return type("D", (), {
            "__len__": lambda self: 2,
            "__iter__": lambda self: iter([_Page(), _Page()]),
            "close": lambda self: None,
        })()

    import app.services.bank_statement_parser as bsp

    monkeypatch.setattr(bsp, "fitz", MagicMock(open=_open), raising=False)

    # Inject fitz via import inside method — patch builtins path by stubbing module
    import sys
    import types

    fake_fitz = types.SimpleNamespace(open=_open)
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

    out = await parser.parse_pdf_statement(
        "synthetic.pdf",
        bank_hint="UNKNOWN",
        bank_id_already_attempted=True,
    )
    assert id_calls["n"] == 0
    assert out["parse_status"] == STATUS_BANK_SELECTION_REQUIRED
    assert out["transactions"] == []
    assert out["fallback_allowed"] is False


@pytest.mark.asyncio
async def test_unknown_scanned_creates_no_page_vlm(monkeypatch):
    parser = BankStatementParser.__new__(BankStatementParser)
    monkeypatch.setattr(parser, "_detect_bank", lambda _t: "UNKNOWN")
    monkeypatch.setattr(parser, "_emit_progress", lambda *a, **k: None)
    vlm = AsyncMock(return_value=[{"deposit": 1}])
    monkeypatch.setattr(parser, "_parse_with_ocr_fallback", vlm)

    import types, sys

    class _Page:
        def get_text(self):
            return ""

    def _open(_path):
        return type("D", (), {
            "__len__": lambda self: 3,
            "__iter__": lambda self: iter([_Page(), _Page(), _Page()]),
        })()

    monkeypatch.setitem(sys.modules, "fitz", types.SimpleNamespace(open=_open))

    out = await parser.parse_pdf_statement(
        "synthetic.pdf",
        bank_hint="UNKNOWN",
        bank_id_already_attempted=True,
    )
    assert out["parse_status"] == STATUS_BANK_SELECTION_REQUIRED
    assert out["count"] == 0
    vlm.assert_not_awaited()


@pytest.mark.asyncio
async def test_hsbc_abstain_never_enters_generic_fallback(monkeypatch):
    parser = BankStatementParser.__new__(BankStatementParser)
    monkeypatch.setattr(parser, "_detect_bank", lambda _t: "HSBC")
    monkeypatch.setattr(parser, "_emit_progress", lambda *a, **k: None)
    monkeypatch.setattr(
        parser, "_parse_hsbc_statement", AsyncMock(return_value=[])
    )
    vlm = AsyncMock(return_value=[{"deposit": 9.0, "description": "FAB"}])
    monkeypatch.setattr(parser, "_parse_with_ocr_fallback", vlm)

    import types, sys

    class _Page:
        def get_text(self):
            return ""

    def _open(_path):
        return type("D", (), {
            "__len__": lambda self: 1,
            "__iter__": lambda self: iter([_Page()]),
        })()

    monkeypatch.setitem(sys.modules, "fitz", types.SimpleNamespace(open=_open))

    out = await parser.parse_pdf_statement(
        "synthetic.pdf",
        bank_hint="HSBC",
        bank_id_already_attempted=True,
    )
    assert out["parse_status"] == STATUS_ABSTAINED_NEEDS_LAYOUT
    assert out["transactions"] == []
    vlm.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_content_not_retried_and_same_endpoint_fallback_skipped(
    monkeypatch,
):
    monkeypatch.setenv("OCR_PRIMARY_RETRYABLE_MAX_RETRIES", "1")
    monkeypatch.setenv("VLM_MODEL", "same-model")

    class _Empty:
        def __init__(self):
            self.calls = 0
            self._base_url = "https://same/v1"
            self._model = "same-model"

        async def recognize(self, *_a, **_k):
            self.calls += 1
            raise RuntimeError("OCR_EMPTY_CONTENT: empty")

    class _Registry:
        def __init__(self, m):
            self._m = m

        def get(self, name):
            return self._m[name]

    svc = OcrService()
    primary = _Empty()
    same_fb = _Empty()
    svc._registry = _Registry(  # type: ignore[attr-defined]
        {"qwen-vl-ocr-latest": primary, "DeepSeek-OCR": same_fb}
    )
    with pytest.raises(RuntimeError) as exc:
        await svc.recognize("x.png", provider_name="qwen-vl-ocr-latest", model="same-model")
    assert primary.calls == 1  # no empty-content retry
    assert same_fb.calls == 0  # same endpoint/model skipped
    assert "no fallbacks attempted" in str(exc.value) or "Fallbacks failed" in str(exc.value)


@pytest.mark.asyncio
async def test_bank_id_recognize_disables_retry_fallback(monkeypatch):
    class _Empty:
        def __init__(self):
            self.calls = 0
            self._base_url = "https://x/v1"
            self._model = "m"

        async def recognize(self, *_a, **_k):
            self.calls += 1
            raise RuntimeError("OCR_EMPTY_CONTENT: empty")

    class _Registry:
        def __init__(self, m):
            self._m = m

        def get(self, name):
            return self._m[name]

    svc = OcrService()
    p = _Empty()
    svc._registry = _Registry({"qwen-vl-ocr-latest": p, "DeepSeek-OCR": _Empty()})  # type: ignore
    with pytest.raises(RuntimeError):
        await svc.recognize(
            "x.png",
            provider_name="qwen-vl-ocr-latest",
            model="m",
            allow_retry=False,
            allow_fallback=False,
        )
    assert p.calls == 1


@pytest.mark.asyncio
async def test_provider_failed_pages_excluded_from_r2_zero_set():
    """Document the R2 gate: provider_failed density flag removes zero pages."""
    zero_pages_raw = {0, 1, 2}
    page_density_map = {1: {"provider_failed": True}}
    provider_failed_pages = {
        pn for pn in range(3) if page_density_map.get(pn, {}).get("provider_failed")
    }
    assert provider_failed_pages == {1}
    assert (zero_pages_raw - provider_failed_pages) == {0, 2}

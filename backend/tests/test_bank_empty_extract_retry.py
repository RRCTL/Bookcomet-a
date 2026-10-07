"""Auto-retry path for dense empty-extract miss (fiction-only; no real PDFs)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api import ocr as ocr_mod
from app.services.bank_empty_extract import EMPTY_EXTRACT_STATUS, assess_bank_page_extract


_FICTIONAL_DENSE = "\n".join(
    [
        "Fictional activity header Date Deposit Withdrawal Balance 存入 提取 日期",
        *[f"fictional dense line {i} statement text" for i in range(90)],
    ]
)


class _FakeOcr:
    def __init__(self, text: str, lines: int) -> None:
        self.text = text
        self.lines = [SimpleNamespace(text=f"L{i}", confidence=0.9) for i in range(lines)]


@pytest.mark.asyncio
async def test_auto_retry_invoked_on_dense_empty_then_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def fake_enhance(*_a, **_k):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"raw_text": "hdr\n", "tsv_rows": []}
        return {
            "raw_text": "No.\t日期\t存入\t提取\t備註\n1\t2020-08-11\t100.00\t\tFictional credit\n",
            "tsv_rows": [
                {
                    "No.": "1",
                    "日期": "2020-08-11",
                    "存入": "100.00",
                    "提取": "",
                    "備註": "Fictional credit",
                }
            ],
            "output_format": "tsv",
        }

    monkeypatch.setattr(ocr_mod, "_document_type_for_enhancement", lambda *a, **k: "bank_statement_page")
    monkeypatch.setattr(ocr_mod, "_load_company_context", lambda *a, **k: {})
    monkeypatch.setattr(ocr_mod, "_inject_trace_meta", lambda *a, **k: None)
    monkeypatch.setattr(ocr_mod, "_apply_rules_from_memory", lambda rows, *a, **k: rows)
    monkeypatch.setattr(ocr_mod, "_apply_exclusions", lambda rows, *a, **k: rows)
    monkeypatch.setattr(
        ocr_mod._ai_processor,
        "enhance_ocr_result",
        AsyncMock(side_effect=fake_enhance),
    )

    ocr_result = _FakeOcr(_FICTIONAL_DENSE, lines=120)
    enhanced, assessment = await ocr_mod._enhance_bank_ocr_with_empty_extract_retry(
        ocr_result=ocr_result,
        page_num=2,
        processing_mode="BANK",
        company_id="co-fictional",
        db=SimpleNamespace(),
        trace_id="trace-fictional",
        rule_md=None,
        excl_rules=None,
    )

    assert calls["n"] == 2
    assert assessment.needs_retry is False
    assert assessment.activity_row_count == 1
    assert isinstance(enhanced, dict)
    assert len(enhanced.get("tsv_rows") or []) == 1


@pytest.mark.asyncio
async def test_auto_retry_still_empty_marks_needs_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def always_empty(*_a, **_k):
        calls["n"] += 1
        return {"raw_text": "short", "tsv_rows": []}

    monkeypatch.setattr(ocr_mod, "_document_type_for_enhancement", lambda *a, **k: "bank_statement_page")
    monkeypatch.setattr(ocr_mod, "_load_company_context", lambda *a, **k: {})
    monkeypatch.setattr(ocr_mod, "_inject_trace_meta", lambda *a, **k: None)
    monkeypatch.setattr(ocr_mod, "_apply_rules_from_memory", lambda rows, *a, **k: rows)
    monkeypatch.setattr(ocr_mod, "_apply_exclusions", lambda rows, *a, **k: rows)
    monkeypatch.setattr(
        ocr_mod._ai_processor,
        "enhance_ocr_result",
        AsyncMock(side_effect=always_empty),
    )

    ocr_result = _FakeOcr(_FICTIONAL_DENSE, lines=150)
    _enhanced, assessment = await ocr_mod._enhance_bank_ocr_with_empty_extract_retry(
        ocr_result=ocr_result,
        page_num=3,
        processing_mode="BANK",
        company_id="co-fictional",
        db=SimpleNamespace(),
        trace_id="trace-fictional",
        rule_md=None,
        excl_rules=None,
    )

    assert calls["n"] == 2
    assert assessment.needs_retry is True
    page = {"page": 3}
    from app.services.bank_empty_extract import apply_empty_extract_flags

    apply_empty_extract_flags(page, assessment, auto_retried=True)
    assert page["status"] == EMPTY_EXTRACT_STATUS
    assert page["empty_extract_auto_retried"] is True


def test_assess_short_reply_supporting_signal() -> None:
    a = assess_bank_page_extract(
        ocr_text=_FICTIONAL_DENSE,
        lines_count=174,
        ai_enhanced={"raw_text": "x" * 94, "tsv_rows": []},
    )
    assert a.short_reply is True
    assert a.needs_retry is True

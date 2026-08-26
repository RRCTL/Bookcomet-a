"""Slice 1: HSBC probe/dispatcher must never route into OCR Scenario D.

Synthetic PDF text only — no real statement attachments.
"""
from __future__ import annotations

import inspect
from pathlib import Path

from app.services.bank_document_dispatcher import (
    bank_ocr_response_from_parser_result,
    inspect_bank_pdf,
    route_forbids_scenario_d,
)


def _make_pdf_with_text(path: str, pages: list[str]) -> None:
    import fitz

    doc = fitz.open()
    for text in pages:
        page = doc.new_page()
        page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_inspect_hsbc_routes_to_hsbc_adapter(tmp_path):
    pdf = tmp_path / "synthetic_hsbc_probe.pdf"
    _make_pdf_with_text(
        str(pdf),
        [
            "HSBC Business Direct\nPortfolio Summary\n",
            "HSBC Business Direct HKD Savings\nDate Transaction Details Deposit Withdrawal Balance\n",
        ],
    )
    probe = inspect_bank_pdf(str(pdf))
    assert probe.bank_id == "HSBC"
    assert probe.route == "hsbc_adapter"
    assert probe.adapter == "hsbc_adapter_v2"
    assert probe.has_txn_header is True
    assert route_forbids_scenario_d(probe) is True


def test_bank_ocr_response_never_marks_scenario_d():
    out = bank_ocr_response_from_parser_result(
        filename="synthetic.pdf",
        trace_id="t1",
        result={
            "bank": "HSBC",
            "transactions": [],
            "count": 0,
            "dispatcher_route": "hsbc_adapter",
            "dispatcher_adapter": "hsbc_adapter_v2",
            "bank_probe": {"bank_id": "HSBC", "route": "hsbc_adapter"},
        },
    )
    assert out["scenario_d_used"] is False
    assert out["processing_steps"]["scenario_d"] == "skipped"
    assert out["dispatcher_route"] == "hsbc_adapter"
    assert out["provider"] == "bank_document_dispatcher"


def test_ocr_test_core_bank_branch_dispatches_before_scenario_d():
    """Structural integration check: BANK path returns via dispatcher before Scenario D."""
    src = Path(__file__).resolve().parents[1] / "app" / "api" / "ocr.py"
    text = src.read_text(encoding="utf-8")
    bank_idx = text.find('if processing_mode == "BANK":')
    dispatch_idx = text.find("dispatch_bank_pdf", bank_idx if bank_idx >= 0 else 0)
    scenario_d_idx = text.find("Scenario D: parallel processing")
    assert bank_idx >= 0, "ocr_test_core must special-case BANK mode"
    assert dispatch_idx > bank_idx, "BANK branch must call dispatch_bank_pdf"
    assert scenario_d_idx > dispatch_idx, "Scenario D code must remain after BANK early return"
    # Early return in BANK branch
    bank_block = text[bank_idx:scenario_d_idx]
    assert "return out" in bank_block or "return bank" in bank_block.lower()
    assert "scenario_d_used" in bank_block or "bank_ocr_response_from_parser_result" in bank_block

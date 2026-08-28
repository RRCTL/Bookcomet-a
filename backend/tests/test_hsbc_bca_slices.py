"""Slice B/C/A — bank override, layout evidence, visible bank-ID (synthetic only)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.bank_id_content import visible_bank_id_from_content
from app.services.bank_override import (
    bank_override_from_graph,
    normalize_bank_override,
    probe_fields_for_bank,
)
from app.services.hsbc_admission import admit_page_candidates
from app.services.hsbc_layout_evidence import (
    LayoutToken,
    build_amount_anchors,
    detect_column_bands,
    merge_cr_dr_same_row,
    parse_amount,
    tokens_from_pymupdf_words,
)


def test_normalize_bank_override_rejects_unknown_and_filename_shaped():
    assert normalize_bank_override(None) is None
    assert normalize_bank_override("") is None
    assert normalize_bank_override("UNKNOWN") is None
    assert normalize_bank_override("HSBC") == "HSBC"
    assert normalize_bank_override("hang_seng") == "HANG_SENG"
    # Must not treat arbitrary strings / filename stems as banks
    assert normalize_bank_override("MMC-HSBC-DEC25") is None
    assert normalize_bank_override("not_a_bank") is None


def test_bank_override_from_graph_reads_top_level_and_mode_node():
    assert bank_override_from_graph(None) is None
    assert bank_override_from_graph({"bank_override": "HSBC"}) == "HSBC"
    assert (
        bank_override_from_graph(
            {
                "nodes": [
                    {"type": "ModeConfig", "data": {"bank_override": "boc"}},
                ]
            }
        )
        == "BOC"
    )
    # Filename-shaped values on the graph are rejected
    assert bank_override_from_graph({"bank_override": "MMC-HSBC-DEC25"}) is None


def test_probe_fields_for_hsbc():
    route, adapter = probe_fields_for_bank("HSBC")
    assert route == "hsbc_adapter"
    assert adapter == "hsbc_adapter_v2"


def test_visible_bank_id_ignores_empty_and_accepts_json():
    assert visible_bank_id_from_content("") is None
    assert visible_bank_id_from_content(None) is None
    assert visible_bank_id_from_content('{"bank_id":"HSBC"}', known={"HSBC"}) == "HSBC"
    assert (
        visible_bank_id_from_content(
            '```json\n{"bank_id": "BOC"}\n```', known={"BOC", "HSBC"}
        )
        == "BOC"
    )
    # Must not invent from non-JSON prose without bank_id
    assert visible_bank_id_from_content("I think this is HSBC", known={"HSBC"}) is None


def test_layout_two_anchors_stay_separate():
    tokens = [
        LayoutToken("Deposit", 640, 100, 700, 112),
        LayoutToken("Withdrawal", 760, 100, 820, 112),
        LayoutToken("Balance", 880, 100, 940, 112),
        LayoutToken("10.00", 640, 140, 690, 152),
        LayoutToken("3.00", 760, 180, 800, 192),
    ]
    bands = detect_column_bands(tokens, page_width=1000.0)
    anchors = build_amount_anchors(tokens, bands, page_index_1based=1)
    cr_dr = [a for a in anchors if a.side in {"Cr", "Dr"}]
    assert len(cr_dr) == 2
    cands = merge_cr_dr_same_row(anchors)
    result = admit_page_candidates(candidates=cands, amount_anchor_count=2)
    assert len(result.canonical_rows) == 2
    assert result.canonical_rows[0]["row_anchor_id"] != result.canonical_rows[1]["row_anchor_id"]


def test_layout_dual_amount_not_canonical():
    tokens = [
        LayoutToken("Deposit", 640, 100, 700, 112),
        LayoutToken("Withdrawal", 760, 100, 820, 112),
        LayoutToken("1.00", 640, 140, 680, 152),
        LayoutToken("2.00", 760, 141, 800, 153),
    ]
    bands = detect_column_bands(tokens, page_width=1000.0)
    anchors = build_amount_anchors(tokens, bands)
    cands = merge_cr_dr_same_row(anchors)
    result = admit_page_candidates(candidates=cands, amount_anchor_count=2)
    assert result.canonical_rows == []
    assert len(result.unresolved_anchors) >= 1


def test_tokens_from_pymupdf_words_and_parse_amount():
    words = [(10, 10, 20, 20, "12.50", 0, 0, 0)]
    toks = tokens_from_pymupdf_words(words)
    assert len(toks) == 1
    assert parse_amount("1,234.56") == 1234.56
    assert parse_amount("abc") is None


@pytest.mark.asyncio
async def test_dispatch_override_skips_image_id(monkeypatch):
    from app.services import bank_document_dispatcher as disp

    monkeypatch.setattr(
        disp,
        "inspect_bank_pdf",
        lambda _p, **_k: disp.BankProbe(
            bank_id="UNKNOWN",
            text_rich=False,
            has_txn_header=False,
            char_count=0,
            route="generic_bank",
            adapter="generic_bank_adapter",
        ),
    )
    id_mock = AsyncMock(return_value="HSBC")
    parse_mock = AsyncMock(
        return_value={
            "bank": "HSBC",
            "transactions": [],
            "count": 0,
            "parse_status": "abstained_needs_layout",
            "pages_processed": 1,
        }
    )

    class _P:
        _identify_bank_from_image = id_mock
        parse_statement = parse_mock

    monkeypatch.setattr(
        "app.services.bank_statement_parser.BankStatementParser",
        lambda: _P(),
    )
    monkeypatch.setattr(
        "app.core.config.require_bank_vlm_settings",
        lambda: {"model": "x", "api_key": "k", "api_url": "http://x"},
    )

    out = await disp.dispatch_bank_pdf(
        "synthetic.pdf",
        bank_override="HSBC",
    )
    id_mock.assert_not_awaited()
    assert out["bank_override_applied"] == "HSBC"
    assert out.get("user_message")
    assert "layout OCR" in out["user_message"]

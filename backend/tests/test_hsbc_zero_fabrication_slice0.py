"""HSBC Slice-0 zero-fabrication admission — synthetic fixtures only."""

from __future__ import annotations

import pytest

from app.services.hsbc_admission import (
    FLAG_MISSING_AMOUNT_EVIDENCE,
    FLAG_NEEDS_LAYOUT_REVIEW,
    FLAG_UNRESOLVED_ANCHOR,
    FLAG_VLM_FINANCIAL_ABSTAINED,
    PAGE_STATUS_NEEDS_LAYOUT_REVIEW,
    admit_page_candidates,
    export_blocked_by_admission,
    has_deterministic_amount_evidence,
    hsbc_ar_manager_allowed,
    may_emit_transaction,
    row_has_amount_evidence,
)
from app.services.hsbc_contracts import export_blocked_by_contracts


def test_activity_without_layout_evidence_yields_zero_canonical():
    """VLM returns transactions but no trusted amount anchors ⇒ abstain."""
    vlm_only = [
        {
            "description": "SYNTHETIC_A",
            "deposit": 10.0,
            "withdrawal": None,
            "balance": None,
            # no row_anchor_id / numeric_token_ids / column_provenance
        },
        {
            "description": "SYNTHETIC_B",
            "deposit": None,
            "withdrawal": 5.0,
        },
    ]
    assert has_deterministic_amount_evidence(amount_anchor_count=0) is False
    result = admit_page_candidates(candidates=vlm_only, amount_anchor_count=0)
    assert result.abstained is True
    assert result.page_status == PAGE_STATUS_NEEDS_LAYOUT_REVIEW
    assert FLAG_VLM_FINANCIAL_ABSTAINED in result.reason_codes
    assert FLAG_NEEDS_LAYOUT_REVIEW in result.reason_codes
    assert result.canonical_rows == []
    # No physical anchors ⇒ no invented unresolved candidates
    assert result.unresolved_anchors == []


def test_vlm_only_rows_never_exportable_via_contracts_gate():
    result = admit_page_candidates(
        candidates=[{"deposit": 1.0, "description": "X"}],
        amount_anchor_count=0,
    )
    assert export_blocked_by_admission(result.canonical_rows) is False
    # Page abstention itself is tracked via page_verification; rows stay empty.
    assert result.canonical_rows == []


def test_two_adjacent_evidence_backed_anchors_stay_separate():
    a1 = {
        "row_anchor_id": "synth-a1",
        "_hsbc_row_id": "synth-a1",
        "deposit": 10.0,
        "withdrawal": None,
        "numeric_token_ids": ["synth-a1:Cr:10"],
        "column_provenance": {"deposit": "prescan_cr", "withdrawal": None, "balance": None},
        "description": "SYNTH_DESC_1",
        "row_kind": "transaction",
    }
    a2 = {
        "row_anchor_id": "synth-a2",
        "_hsbc_row_id": "synth-a2",
        "deposit": None,
        "withdrawal": 3.0,
        "numeric_token_ids": ["synth-a2:Dr:3"],
        "column_provenance": {"deposit": None, "withdrawal": "prescan_dr", "balance": None},
        "description": "SYNTH_DESC_2",
        "row_kind": "transaction",
    }
    result = admit_page_candidates(candidates=[a1, a2], amount_anchor_count=2)
    assert result.abstained is False
    assert len(result.canonical_rows) == 2
    assert result.canonical_rows[0]["row_anchor_id"] != result.canonical_rows[1]["row_anchor_id"]


def test_may_emit_rejects_dual_amount_and_bare_vlm_row():
    bare = {"deposit": 9.0, "withdrawal": None, "description": "BARE"}
    assert may_emit_transaction(bare) is False
    dual = {
        "row_anchor_id": "x",
        "numeric_token_ids": ["x:Cr:1"],
        "column_provenance": {"deposit": "prescan_cr"},
        "deposit": 1.0,
        "withdrawal": 2.0,
    }
    assert may_emit_transaction(dual) is False


def test_unresolved_anchor_only_when_physical_region_known():
    """With amount evidence present but row fails admission → unresolved if anchor id."""
    bad = {
        "row_anchor_id": "synth-u1",
        "_hsbc_row_id": "synth-u1",
        # evidence markers present but dual amounts fail may_emit
        "numeric_token_ids": ["synth-u1:Cr:1"],
        "column_provenance": {"deposit": "prescan_cr", "withdrawal": "prescan_dr"},
        "deposit": 1.0,
        "withdrawal": 2.0,
        "row_kind": "transaction",
    }
    result = admit_page_candidates(candidates=[bad], amount_anchor_count=1)
    assert result.canonical_rows == []
    assert len(result.unresolved_anchors) == 1
    u = result.unresolved_anchors[0]
    assert u.get("exportable") is False
    assert FLAG_UNRESOLVED_ANCHOR in (u.get("validation_flags") or [])
    assert export_blocked_by_contracts([u]) is True


def test_ar_manager_blocked_without_evidence():
    assert hsbc_ar_manager_allowed([]) is False
    assert hsbc_ar_manager_allowed([{"deposit": 1.0}]) is False
    ok = {
        "row_anchor_id": "a",
        "numeric_token_ids": ["a:Cr:1"],
        "column_provenance": {"deposit": "prescan_cr"},
    }
    assert hsbc_ar_manager_allowed([ok]) is True
    assert row_has_amount_evidence(ok) is True


def test_balance_change_without_amount_token_not_emitted():
    """Balances differ but no amount token ⇒ no ordinary transaction."""
    row = {
        "row_anchor_id": "b1",
        "deposit": None,
        "withdrawal": None,
        "balance": None,
        # no numeric_token_ids / provenance
    }
    assert may_emit_transaction(row) is False
    result = admit_page_candidates(candidates=[row], amount_anchor_count=0)
    assert result.canonical_rows == []
    assert FLAG_MISSING_AMOUNT_EVIDENCE in result.reason_codes


def test_day_end_balance_same_anchor_admits_amount_not_balance_derived():
    """Deposit token + balance on one evidence-backed anchor ⇒ one canonical row."""
    row = {
        "row_anchor_id": "synth-day-end",
        "_hsbc_row_id": "synth-day-end",
        "deposit": 12.5,
        "withdrawal": None,
        "balance": 100.0,
        "numeric_token_ids": ["synth-day-end:Cr:12.5", "synth-day-end:Bal:100"],
        "column_provenance": {
            "deposit": "prescan_cr",
            "withdrawal": None,
            "balance": "prescan_balance_band",
        },
        "has_balance_band_token": True,
        "row_kind": "transaction",
        "description": "SYNTH_DAY_END",
    }
    assert may_emit_transaction(row) is True
    result = admit_page_candidates(candidates=[row], amount_anchor_count=1)
    assert len(result.canonical_rows) == 1
    assert result.canonical_rows[0]["deposit"] == 12.5
    assert result.canonical_rows[0]["withdrawal"] is None


def test_mark_page_needs_layout_review_and_v1_demotion_contract():
    from app.services.hsbc_admission import mark_page_needs_layout_review

    out: dict[int, str] = {}
    mark_page_needs_layout_review(out, 3)
    assert out[3] == PAGE_STATUS_NEEDS_LAYOUT_REVIEW
    mark_page_needs_layout_review(None, 1)  # no-op


@pytest.mark.asyncio
async def test_v1_process_page_never_emits_financial_rows(monkeypatch):
    """Slice 0: V1 path returns [] and marks needs_layout_review (no VLM call)."""
    from app.services.bank_statement_parser import BankStatementParser

    calls = {"vlm": 0}

    async def _boom(*_a, **_k):
        calls["vlm"] += 1
        raise AssertionError("V1 must not call VLM for financial emission under Slice 0")

    monkeypatch.setattr(
        BankStatementParser,
        "_hsbc_prescan_count",
        staticmethod(lambda _page: (2, 1, 3)),
    )
    monkeypatch.setattr(BankStatementParser, "_run_vlm_track", _boom)

    page_verification: dict[int, str] = {}
    parser = BankStatementParser.__new__(BankStatementParser)
    result = await parser._hsbc_process_page(
        page=object(),
        page_num=0,
        page_count=1,
        specific_prompt="unused",
        default_prompt="unused",
        vlm_model="unused-model",
        company_identity=None,
        page_verification_out=page_verification,
    )
    assert result == []
    assert calls["vlm"] == 0
    assert page_verification[1] == PAGE_STATUS_NEEDS_LAYOUT_REVIEW

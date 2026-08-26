"""HSBC needs_review / balance-missing semantics — synthetic fixtures only."""

from __future__ import annotations

import inspect

from app.services import hsbc_balance_policy as policy
from app.services.extraction_validation import finalize_bank_transactions, validate_bank_transaction
from app.services.hsbc_contracts import apply_contracts_to_row, validate_contract_b_column_band


def _hsbc_txn(**overrides):
    """Ordinary HSBC transaction row with structured identity (no loose name match)."""
    row = {
        "transaction_date": "2099-01-01",
        "description": "SYNTHETIC_DESC",
        "deposit": 10.0,
        "withdrawal": None,
        "balance": None,
        "currency": "HKD",
        "bank_id": policy.HSBC_BANK_ID,
        "layout_profile": policy.HSBC_LAYOUT_PROFILE_DAY_END_BALANCE,
        "parser_adapter": "hsbc_adapter_v2",
        "row_kind": policy.ROW_KIND_TRANSACTION,
        "row_anchor_id": "synth-anchor-a",
        "_hsbc_row_id": "synth-anchor-a",
        "section_id": "synth-sec-a",
        "source_page": 1,
        "column_provenance": {
            "deposit": "prescan_cr",
            "withdrawal": None,
            "balance": None,
        },
        "numeric_token_ids": ["synth-anchor-a:Cr:10.0"],
        "has_balance_band_token": False,
    }
    row.update(overrides)
    return row


def test_a_non_day_end_blank_balance_not_needs_review():
    """Blank balance on a non-day-end row in a resolved date group is expected."""
    mid = _hsbc_txn(row_anchor_id="synth-a1", _hsbc_row_id="synth-a1", deposit=10.0)
    day_end = _hsbc_txn(
        row_anchor_id="synth-a2",
        _hsbc_row_id="synth-a2",
        deposit=None,
        withdrawal=5.0,
        balance=100.0,
        has_balance_band_token=True,
        column_provenance={
            "deposit": None,
            "withdrawal": "prescan_dr",
            "balance": "prescan_balance_band",
        },
    )
    out = finalize_bank_transactions([mid, day_end])
    assert out[0].get("is_date_group_day_end") is False
    assert out[0].get("date_group_resolved") is True
    assert out[0].get("needs_review") is False
    assert out[0].get("balance_missing_expected") is True
    assert "bank_balance_missing_expected" in (out[0].get("validation_flags") or [])
    assert "bank_balance_missing" not in (out[0].get("validation_flags") or [])
    assert out[0].get("balance") is None


def test_b_day_end_printed_balance_preserved():
    """Day-end row with a printed balance keeps the balance and is not flagged missing."""
    mid = _hsbc_txn(row_anchor_id="synth-b1", _hsbc_row_id="synth-b1", deposit=10.0)
    printed = 1234.56
    day_end = _hsbc_txn(
        row_anchor_id="synth-b2",
        _hsbc_row_id="synth-b2",
        deposit=20.0,
        balance=printed,
        has_balance_band_token=True,
        column_provenance={
            "deposit": "prescan_cr",
            "withdrawal": None,
            "balance": "prescan_balance_band",
        },
        numeric_token_ids=["synth-b2:Cr:20.0", "synth-b2:Bal:1234.56"],
    )
    out = finalize_bank_transactions([mid, day_end])
    assert out[1].get("is_date_group_day_end") is True
    assert out[1].get("balance") == printed
    assert out[1].get("needs_review") is False
    assert "bank_balance_missing" not in (out[1].get("validation_flags") or [])
    assert "bank_balance_missing_expected" not in (out[1].get("validation_flags") or [])


def test_c_ambiguous_x_band_needs_review():
    mid = _hsbc_txn(row_anchor_id="synth-c1", _hsbc_row_id="synth-c1")
    day_end = _hsbc_txn(
        row_anchor_id="synth-c2",
        _hsbc_row_id="synth-c2",
        deposit=None,
        withdrawal=5.0,
        balance=50.0,
        has_balance_band_token=True,
    )
    tokens = [
        {
            "column": "Cr",
            "band": "deposit",
            "amount": 10.0,
            "ambiguous": True,
            "band_confidence": 0.2,
        }
    ]
    mid = apply_contracts_to_row(mid, tokens=tokens)
    assert mid.get("needs_review") is True
    assert "ambiguous_column_band" in (mid.get("validation_flags") or [])

    finalized = finalize_bank_transactions([mid, day_end])
    assert finalized[0].get("needs_review") is True
    assert "ambiguous_column_band" in (finalized[0].get("validation_flags") or [])
    assert "bank_balance_missing_expected" in (finalized[0].get("validation_flags") or [])


def test_d_deposit_and_withdrawal_both_set_needs_review():
    mid = _hsbc_txn(
        row_anchor_id="synth-d1",
        _hsbc_row_id="synth-d1",
        deposit=10.0,
        withdrawal=5.0,
    )
    day_end = _hsbc_txn(
        row_anchor_id="synth-d2",
        _hsbc_row_id="synth-d2",
        deposit=None,
        withdrawal=1.0,
        balance=9.0,
        has_balance_band_token=True,
    )
    out = finalize_bank_transactions([mid, day_end])
    assert out[0].get("needs_review") is True
    assert "bank_deposit_and_withdrawal_both_set" in (out[0].get("validation_flags") or [])
    assert "bank_balance_missing_expected" in (out[0].get("validation_flags") or [])
    assert "bank_balance_missing" not in (out[0].get("validation_flags") or [])

    dual = validate_contract_b_column_band(row=mid, tokens=None)
    assert dual.ok is False
    assert "amount_conflict" in dual.flags


def test_e_balance_band_token_assigned_to_amount_needs_review():
    row = _hsbc_txn(
        deposit=None,
        withdrawal=99.0,
        balance=None,
        column_provenance={
            "deposit": None,
            "withdrawal": "prescan_dr",
            "balance": None,
        },
    )
    tokens = [{"column": "Dr", "band": "balance", "amount": 99.0}]
    res = validate_contract_b_column_band(row=row, tokens=tokens)
    assert res.ok is False
    assert "column_band_violation" in res.flags

    out = apply_contracts_to_row(row, tokens=tokens)
    assert out.get("needs_review") is True
    assert "column_band_violation" in (out.get("validation_flags") or [])


def test_f_missing_balance_on_last_row_of_resolved_date_group():
    """Last row of a resolved date group without balance is a real validation issue."""
    mid = _hsbc_txn(row_anchor_id="synth-f1", _hsbc_row_id="synth-f1", deposit=10.0)
    last = _hsbc_txn(
        row_anchor_id="synth-f2",
        _hsbc_row_id="synth-f2",
        deposit=None,
        withdrawal=3.0,
        balance=None,
        has_balance_band_token=False,
    )
    out = finalize_bank_transactions([mid, last])
    assert out[1].get("is_date_group_day_end") is True
    assert out[1].get("date_group_resolved") is True
    assert out[1].get("needs_review") is True
    assert "bank_balance_missing" in (out[1].get("validation_flags") or [])
    assert out[1].get("balance_missing_expected") is not True
    assert "bank_balance_missing_expected" not in (out[1].get("validation_flags") or [])

    # Mid-day still expected-missing
    assert out[0].get("needs_review") is False
    assert out[0].get("balance_missing_expected") is True


def test_g_bf_and_snapshot_rows_are_separate_kinds():
    """B/F / snapshot: deposit+withdrawal null, balance retained; not ordinary txn."""
    bf = {
        "transaction_date": "2099-01-01",
        "description": "B/F BALANCE",
        "deposit": None,
        "withdrawal": None,
        "balance": 500.0,
        "row_kind": policy.ROW_KIND_BROUGHT_FORWARD,
        "bank_id": policy.HSBC_BANK_ID,
        "layout_profile": policy.HSBC_LAYOUT_PROFILE_DAY_END_BALANCE,
        "parser_adapter": "hsbc_adapter_v2",
        "section_id": "synth-sec-a",
    }
    snap = {
        "transaction_date": None,
        "description": "無交易",
        "deposit": None,
        "withdrawal": None,
        "balance": 500.0,
        "row_kind": policy.ROW_KIND_BALANCE_SNAPSHOT,
        "bank_id": policy.HSBC_BANK_ID,
        "layout_profile": policy.HSBC_LAYOUT_PROFILE_DAY_END_BALANCE,
        "parser_adapter": "hsbc_adapter_v2",
        "section_id": "synth-sec-b",
    }
    out = finalize_bank_transactions([bf, snap])
    assert out[0].get("row_kind") == policy.ROW_KIND_BROUGHT_FORWARD
    assert out[0].get("deposit") is None and out[0].get("withdrawal") is None
    assert out[0].get("balance") == 500.0
    assert out[0].get("balance_missing_expected") is not True
    assert out[0].get("needs_review") is False

    assert out[1].get("row_kind") == policy.ROW_KIND_BALANCE_SNAPSHOT
    assert out[1].get("deposit") is None and out[1].get("withdrawal") is None
    assert out[1].get("balance") == 500.0


def test_h_no_loose_bank_name_string_matching():
    """Policy must use bank_id/layout_profile/parser_adapter — not account_type substrings."""
    # Looks like HSBC by name only — must NOT get expected-missing treatment.
    loose = {
        "transaction_date": "2099-01-01",
        "description": "SYNTHETIC",
        "account_type": "HSBC Business Direct HKD Current",
        "deposit": 10.0,
        "withdrawal": None,
        "balance": None,
        "row_kind": policy.ROW_KIND_TRANSACTION,
        "row_anchor_id": "loose-1",
    }
    assert policy.uses_hsbc_day_end_balance_layout(loose) is False
    vr = validate_bank_transaction(loose)
    assert vr.needs_review is True
    assert "bank_balance_missing" in vr.validation_flags
    assert "bank_balance_missing_expected" not in vr.validation_flags

    # Adapter alone is insufficient without bank_id + layout_profile.
    adapter_only = dict(loose, parser_adapter="hsbc_adapter_v2")
    assert policy.uses_hsbc_day_end_balance_layout(adapter_only) is False

    # Structured identity is required.
    structured = _hsbc_txn()
    assert policy.uses_hsbc_day_end_balance_layout(structured) is True

    # Source of the gate must not rely on scanning account_type for bank names.
    src = inspect.getsource(policy.uses_hsbc_day_end_balance_layout)
    assert "account_type" not in src
    assert "HSBC Business" not in src


def test_unresolved_date_group_explicitly_flagged():
    row = _hsbc_txn(transaction_date=None, balance=None)
    out = finalize_bank_transactions([row])[0]
    assert out.get("date_group_resolved") is False
    assert out.get("needs_review") is True
    assert "date_group_unresolved" in (out.get("validation_flags") or [])
    assert "bank_balance_missing_expected" not in (out.get("validation_flags") or [])
    assert out.get("balance_missing_expected") is not True


def test_balance_attach_requires_y_range_and_x_band():
    """Printed balance attaches only via row-anchor y-range + Balance x-band."""
    ranges = policy.normalize_row_anchor_y_ranges(
        [
            {"row_id": "r1", "y": 100.0},
            {"row_id": "r2", "y": 130.0},
        ],
        page_height=800.0,
    )
    assert ranges["r1"][0] < 100.0 < ranges["r1"][1]
    assert ranges["r2"][0] < 130.0 < ranges["r2"][1]

    # Token in y-range but outside Balance x-band → no attach
    amt, has_tok = policy.find_balance_in_row_anchor_range(
        y_lo=ranges["r2"][0],
        y_hi=ranges["r2"][1],
        balances=[{"y": 130.0, "x": 10.0, "amount": 77.0}],
        balance_x_lo=400.0,
        balance_x_hi=500.0,
    )
    assert amt is None and has_tok is False

    # Token in both y-range and Balance x-band → attach
    amt2, has_tok2 = policy.find_balance_in_row_anchor_range(
        y_lo=ranges["r2"][0],
        y_hi=ranges["r2"][1],
        balances=[{"y": 130.0, "x": 450.0, "amount": 77.0}],
        balance_x_lo=400.0,
        balance_x_hi=500.0,
    )
    assert amt2 == 77.0 and has_tok2 is True

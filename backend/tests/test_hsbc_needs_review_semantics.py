"""HSBC needs_review semantics — synthetic fixtures only (no real statement data)."""

from __future__ import annotations

from app.services.extraction_validation import finalize_bank_transactions, validate_bank_transaction
from app.services.hsbc_contracts import apply_contracts_to_row, validate_contract_b_column_band


def _hsbc_base_row(**overrides):
    """Minimal HSBC-shaped row with provenance; amounts/dates are synthetic placeholders."""
    row = {
        "transaction_date": "2099-01-01",
        "description": "SYNTHETIC_DESC",
        "deposit": 10.0,
        "withdrawal": None,
        "balance": None,
        "currency": "HKD",
        "parser_adapter": "hsbc_adapter_v2",
        "_hsbc_row_id": "synth-anchor-a",
        "_hsbc_section_id": "synth-sec-a",
        "source_page": 1,
        "section_id": "synth-sec-a",
        "row_anchor_id": "synth-anchor-a",
        "column_provenance": {
            "deposit": "prescan_cr",
            "withdrawal": None,
            "balance": None,
        },
        "numeric_token_ids": ["synth-anchor-a:Cr:10.0"],
        "balance_missing_expected": True,
    }
    row.update(overrides)
    return row


def test_a_non_day_end_blank_balance_not_needs_review():
    """Blank balance on a non-day-end HSBC row is expected — not a review failure."""
    row = _hsbc_base_row(balance=None, balance_missing_expected=True)
    vr = validate_bank_transaction(row)
    assert vr.needs_review is False
    assert "bank_balance_missing" not in vr.validation_flags
    assert "bank_balance_missing_expected" in vr.validation_flags

    finalized = finalize_bank_transactions([row])[0]
    assert finalized.get("needs_review") is False
    assert finalized.get("balance_missing_expected") is True
    assert finalized.get("balance") is None


def test_b_day_end_printed_balance_preserved():
    """Day-end row with a printed balance keeps the balance and is not flagged missing."""
    printed = 1234.56
    row = _hsbc_base_row(
        balance=printed,
        balance_missing_expected=False,
        column_provenance={
            "deposit": "prescan_cr",
            "withdrawal": None,
            "balance": "prescan_balance_band",
        },
        numeric_token_ids=["synth-anchor-b:Cr:10.0", "synth-anchor-b:Bal:1234.56"],
    )
    row.pop("balance_missing_expected", None)
    vr = validate_bank_transaction(row)
    assert vr.needs_review is False
    assert "bank_balance_missing" not in vr.validation_flags
    assert "bank_balance_missing_expected" not in vr.validation_flags

    finalized = finalize_bank_transactions([row])[0]
    assert finalized.get("balance") == printed
    assert finalized.get("needs_review") is False


def test_c_ambiguous_x_band_needs_review():
    row = _hsbc_base_row(balance=None)
    tokens = [
        {
            "column": "Cr",
            "band": "deposit",
            "amount": 10.0,
            "ambiguous": True,
            "band_confidence": 0.2,
        }
    ]
    out = apply_contracts_to_row(row, tokens=tokens)
    assert out.get("needs_review") is True
    assert "ambiguous_column_band" in (out.get("validation_flags") or [])

    # finalize must not clear the real review reason
    finalized = finalize_bank_transactions([out])[0]
    assert finalized.get("needs_review") is True
    assert "ambiguous_column_band" in (finalized.get("validation_flags") or [])
    assert "bank_balance_missing_expected" in (finalized.get("validation_flags") or [])


def test_d_deposit_and_withdrawal_both_set_needs_review():
    row = _hsbc_base_row(deposit=10.0, withdrawal=5.0, balance=None)
    vr = validate_bank_transaction(row)
    assert vr.needs_review is True
    assert "bank_deposit_and_withdrawal_both_set" in vr.validation_flags
    # Expected blank balance remains informational alongside the real failure.
    assert "bank_balance_missing_expected" in vr.validation_flags
    assert "bank_balance_missing" not in vr.validation_flags

    dual = validate_contract_b_column_band(row=row, tokens=None)
    assert dual.ok is False
    assert "amount_conflict" in dual.flags


def test_e_balance_band_token_assigned_to_amount_needs_review():
    row = _hsbc_base_row(
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


def test_non_hsbc_blank_balance_still_needs_review():
    """Non-HSBC banks keep the blocking missing-balance review flag."""
    row = {
        "transaction_date": "2099-01-01",
        "description": "SYNTHETIC_OTHER",
        "deposit": 1.0,
        "withdrawal": None,
        "balance": None,
    }
    vr = validate_bank_transaction(row)
    assert vr.needs_review is True
    assert "bank_balance_missing" in vr.validation_flags
    assert "bank_balance_missing_expected" not in vr.validation_flags

"""HSBC contracts A–D with synthetic fixtures only (no real statement data)."""

from __future__ import annotations

from app.services.hsbc_contracts import (
    apply_contracts_to_row,
    export_blocked_by_contracts,
    validate_contract_a_coverage,
    validate_contract_b_column_band,
    validate_contract_c_section,
    validate_contract_d_provenance,
)


def test_hsbc_activity_continuation_fixture_contract_a():
    """Continuation page: header+anchors with zero emitted rows ⇒ coverage_failed."""
    ok = validate_contract_a_coverage(
        has_txn_header=True,
        amount_anchor_count=4,
        emitted_row_count=0,
        section_id="sec-savings",
    )
    assert ok.ok is False
    assert "coverage_failed" in ok.flags

    passed = validate_contract_a_coverage(
        has_txn_header=True,
        amount_anchor_count=4,
        emitted_row_count=4,
        section_id="sec-savings",
    )
    assert passed.ok is True


def test_hsbc_day_end_balance_fixture_contract_b():
    """Amount + rightmost balance: correct roles, no dual amount."""
    row = {
        "deposit": 100.0,
        "withdrawal": None,
        "balance": 1100.0,
        "row_anchor_id": "anchor-1",
        "section_id": "sec-a",
        "column_provenance": {
            "deposit": "prescan_cr",
            "withdrawal": None,
            "balance": "prescan_balance_band",
        },
    }
    tokens = [
        {"column": "Cr", "band": "deposit", "amount": 100.0},
        {"column": "Bal", "band": "balance", "amount": 1100.0},
    ]
    res = validate_contract_b_column_band(row=row, tokens=tokens)
    assert res.ok is True

    bad = dict(row, deposit=100.0, withdrawal=25.0)
    dual = validate_contract_b_column_band(row=bad, tokens=tokens)
    assert dual.ok is False
    assert "amount_conflict" in dual.flags

    swapped_tokens = [{"column": "Dr", "band": "balance", "amount": 1100.0}]
    swapped = validate_contract_b_column_band(
        row={"deposit": None, "withdrawal": 1100.0, "row_anchor_id": "a2"},
        tokens=swapped_tokens,
    )
    assert swapped.ok is False
    assert "column_band_violation" in swapped.flags


def test_hsbc_ambiguous_band_fixture_contract_b():
    row = {"deposit": 50.0, "withdrawal": None, "row_anchor_id": "amb-1"}
    tokens = [{"column": "Cr", "band": "deposit", "amount": 50.0, "ambiguous": True}]
    res = validate_contract_b_column_band(row=row, tokens=tokens)
    assert "needs_review" in res.flags
    assert "ambiguous_column_band" in res.flags
    # Ambiguous is review severity — not a silent guess overwrite
    assert all(i.reason != "guessed_column" for i in res.issues)


def test_hsbc_mixed_sections_fixture_contract_c():
    res = validate_contract_c_section(
        section_id="sec-savings",
        detected_anchor_ids=["a1", "a2", "a3"],
        emitted_anchor_ids=["a1", "a2", "a3"],
        has_balance_only_account=True,
        has_account_balance_snapshot=True,
    )
    assert res.ok is True

    miss_first = validate_contract_c_section(
        section_id="sec-savings",
        detected_anchor_ids=["a1", "a2"],
        emitted_anchor_ids=["a2"],
    )
    assert miss_first.ok is False
    assert "section_first_anchor_missed" in miss_first.flags

    miss_snap = validate_contract_c_section(
        section_id="sec-current",
        detected_anchor_ids=[],
        emitted_anchor_ids=[],
        has_balance_only_account=True,
        has_account_balance_snapshot=False,
    )
    assert miss_snap.ok is False


def test_hsbc_summary_tail_and_provenance_contract_d():
    incomplete = {"deposit": 10.0, "withdrawal": None}
    d = validate_contract_d_provenance(incomplete)
    assert "needs_review" in d.flags

    complete = {
        "source_page": 2,
        "section_id": "sec-a",
        "row_anchor_id": "r1",
        "column_provenance": {"deposit": "prescan_cr"},
        "numeric_token_ids": ["r1:Cr:10.0"],
        "deposit": 10.0,
    }
    assert validate_contract_d_provenance(complete).ok is True

    rewritten = dict(complete, _role_rewritten=True)
    bad = validate_contract_d_provenance(rewritten)
    assert bad.ok is False
    assert "export_role_violation" in bad.flags


def test_export_blocked_when_contracts_fail():
    rows = [
        apply_contracts_to_row(
            {
                "deposit": 1.0,
                "withdrawal": 2.0,
                "row_anchor_id": "x",
                "section_id": "s",
                "source_page": 1,
                "column_provenance": {},
            }
        )
    ]
    assert export_blocked_by_contracts(rows) is True

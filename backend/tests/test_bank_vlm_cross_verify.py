"""Unit tests for bank_vlm_cross_verify (Strategy B merge logic)."""
from app.services.bank_vlm_cross_verify import (
    normalize_txn_date_key,
    reconcile_cross_vlm,
)


def test_reconcile_identical_rows_verified():
    a = [{"date": "2024-01-15", "存入": "100.00", "\u5099\u8a3b": "x"}]
    b = [{"date": "2024-01-15", "deposit": "100.00", "description": "x"}]
    merged, status = reconcile_cross_vlm(a, b)
    assert status == "verified"
    assert len(merged) == 1
    assert merged[0]["verification_status"] == "verified"
    assert merged[0]["存入"] == "100.00"


def test_reconcile_orphan_in_b_needs_review():
    a = [{"date": "2024-01-15", "存入": "100.00"}]
    b = [
        {"date": "2024-01-15", "deposit": "100.00"},
        {"date": "2024-01-16", "deposit": "50.00"},
    ]
    merged, status = reconcile_cross_vlm(a, b)
    assert status == "needs_review"
    assert len(merged) == 2
    assert merged[0]["verification_status"] == "verified"
    assert merged[1]["verification_status"] == "needs_review"
    assert merged[1]["deposit"] == "50.00"


def test_reconcile_amount_mismatch_needs_review():
    a = [{"date": "2024-01-15", "存入": "100.00"}]
    b = [{"date": "2024-01-15", "deposit": "200.00"}]
    merged, status = reconcile_cross_vlm(a, b)
    assert status == "needs_review"
    assert len(merged) == 2


def test_reconcile_duplicate_keys_partial_match():
    a = [
        {"date": "2024-01-15", "存入": "100.00"},
        {"date": "2024-01-15", "存入": "100.00"},
    ]
    b = [{"date": "2024-01-15", "deposit": "100.00"}]
    merged, status = reconcile_cross_vlm(a, b)
    assert status == "needs_review"
    assert len(merged) == 2
    assert sum(1 for r in merged if r["verification_status"] == "needs_review") == 1


def test_normalize_txn_date_key_slash():
    assert normalize_txn_date_key({"日期": "2024/01/15"}) == "20240115"


def test_empty_both_verified():
    merged, status = reconcile_cross_vlm([], [])
    assert status == "verified"
    assert merged == []

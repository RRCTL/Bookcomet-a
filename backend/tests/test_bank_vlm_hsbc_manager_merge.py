"""Tests for HSBC AR manager snapshot + balance-only merge (by row index)."""
from __future__ import annotations

import json

from app.services.bank_vlm_hsbc_manager_merge import (
    build_bookkeeper_snapshot,
    merge_manager_into_bookkeeper,
)


def test_build_bookkeeper_snapshot_truncates_when_many_rows():
    txns = [
        {"transaction_date": "2024-01-01", "deposit": 1.0, "description": "x" * 200}
        for _ in range(20)
    ]
    s = build_bookkeeper_snapshot(txns, desc_max=100, full_if_n_rows=15)
    data = json.loads(s)
    assert len(data) == 20
    assert data[0]["description"].endswith("...")
    assert len(data[0]["description"]) <= 103


def test_build_bookkeeper_snapshot_full_description_when_few_rows():
    long_d = "verbatim " * 30
    txns = [{"transaction_date": "2024-06-15", "withdrawal": 10.0, "description": long_d}]
    s = build_bookkeeper_snapshot(txns, desc_max=50, full_if_n_rows=15)
    data = json.loads(s)
    assert data[0]["description"] == long_d


def test_merge_fills_balance_only_when_bookkeeper_missing():
    bk = [
        {
            "transaction_date": "2024-01-15",
            "deposit": 100.0,
            "withdrawal": None,
            "balance": None,
            "description": "x",
        }
    ]
    mgr = [
        {
            "balance": 5000.0,
            "deposit": 999.0,
            "withdrawal": 888.0,
        }
    ]
    out, misaligned, per_ok = merge_manager_into_bookkeeper(bk, mgr, amount_tolerance=0.02)
    assert not misaligned
    assert per_ok == [True]
    assert out[0]["balance"] == 5000.0
    assert out[0]["deposit"] == 100.0
    assert out[0]["withdrawal"] is None
    assert out[0].get("_ar_manager_amended") is True
    assert out[0].get("_ar_manager_fields") == ["balance"]
    assert "_ar_manager_added" not in out[0]


def test_merge_ignores_manager_deposit_even_if_bookkeeper_empty():
    bk = [
        {
            "transaction_date": "2024-01-15",
            "deposit": None,
            "withdrawal": None,
            "balance": None,
        }
    ]
    mgr = [{"deposit": 100.0, "withdrawal": None, "balance": 1.0}]
    out, _, _ = merge_manager_into_bookkeeper(bk, mgr)
    assert out[0]["deposit"] is None
    assert out[0]["balance"] == 1.0


def test_merge_does_not_overwrite_existing_balance():
    bk = [{"transaction_date": "2024-01-15", "deposit": 10.0, "balance": 100.0}]
    mgr = [{"balance": 999.0}]
    out, _, _ = merge_manager_into_bookkeeper(bk, mgr)
    assert out[0]["balance"] == 100.0
    assert "_ar_manager_amended" not in out[0]


def test_merge_count_mismatch_needs_review():
    bk = [
        {"deposit": 1.0, "balance": None},
        {"deposit": 2.0, "balance": None},
    ]
    mgr = [{"balance": 10.0}]
    out, misaligned, per_ok = merge_manager_into_bookkeeper(bk, mgr)
    assert misaligned
    assert per_ok == [False, False]
    assert len(out) == 2
    assert out[0]["balance"] == 10.0
    assert out[1]["balance"] is None


def test_merge_no_append_manager_only_rows():
    bk = [{"deposit": 1.0, "balance": None}]
    mgr = [{"balance": 5.0}, {"balance": 99.0, "deposit": 77.0}]
    out, misaligned, per_ok = merge_manager_into_bookkeeper(bk, mgr)
    assert misaligned
    assert per_ok == [False]
    assert len(out) == 1
    assert out[0]["balance"] == 5.0
    assert out[0]["deposit"] == 1.0


def test_merge_aligned_balance_disagreement_needs_review_per_row():
    bk = [{"deposit": 10.0, "balance": 100.0}]
    mgr = [{"balance": 999.0}]
    out, misaligned, per_ok = merge_manager_into_bookkeeper(bk, mgr)
    assert not misaligned
    assert per_ok == [False]
    assert out[0]["balance"] == 100.0
    assert "_ar_manager_amended" not in out[0]

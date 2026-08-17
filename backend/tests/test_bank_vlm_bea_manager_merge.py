"""BEA-shaped rows use the same balance-only merge as HSBC."""
from __future__ import annotations

import json

from app.services.bank_vlm_hsbc_manager_merge import (
    build_bookkeeper_snapshot,
    merge_manager_into_bookkeeper,
)


def test_bea_snapshot_includes_account_type():
    txns = [
        {
            "transaction_date": "2026-01-15",
            "deposit": 10.0,
            "withdrawal": None,
            "balance": None,
            "description": "Test",
            "account_type": "HKD CURRENT",
        }
    ]
    snap = build_bookkeeper_snapshot(txns, desc_max=80, full_if_n_rows=15)
    row0 = json.loads(snap)[0]
    assert row0["account_type"] == "HKD CURRENT"


def test_bea_merge_fills_balance_only():
    bk = [
        {
            "transaction_date": "2026-01-15",
            "deposit": 100.0,
            "withdrawal": None,
            "balance": None,
            "description": "In",
            "account_type": "HKD CURRENT",
        }
    ]
    mgr = [{"balance": 5000.0, "deposit": 999.0}]
    out, misaligned, per_ok = merge_manager_into_bookkeeper(bk, mgr)
    assert not misaligned
    assert per_ok == [True]
    assert out[0]["balance"] == 5000.0
    assert out[0]["deposit"] == 100.0
    assert out[0].get("_ar_manager_status") is None

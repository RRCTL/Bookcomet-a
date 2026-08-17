"""OCBC gated amount policy: no invent from poisoned balances; fill from B/F chain."""
from __future__ import annotations

from app.services.ocbc_amount_repair import apply_ocbc_amount_policy


def _row(
    *,
    description: str = "TXN",
    account_type: str = "HKD CURRENT",
    balance=None,
    deposit=None,
    withdrawal=None,
) -> dict:
    return {
        "description": description,
        "備註": description,
        "account_type": account_type,
        "賬戶類型": account_type,
        "currency": "HKD",
        "幣別": "HKD",
        "原幣結餘": "" if balance is None else balance,
        "balance": "" if balance is None else balance,
        "存入": "" if deposit is None else deposit,
        "提取": "" if withdrawal is None else withdrawal,
        "received": "" if deposit is None else deposit,
        "spent": "" if withdrawal is None else withdrawal,
    }


def _amt(txn: dict, *keys: str) -> float | None:
    for k in keys:
        v = txn.get(k)
        if v is None or str(v).strip() == "":
            continue
        return float(str(v).replace(",", ""))
    return None


def test_page20_shape_fills_withdrawals_from_bf_chain():
    """Correct running balances + empty amounts → fill withdrawals after B/F."""
    rows = [
        _row(description="B/F BALANCE", balance=1_090_746.85),
        _row(description="CHQ NO.001530", balance=1_061_296.85),
        _row(description="CHQ NO.001522", balance=1_031_846.85),
        _row(description="OTC SER CHG", balance=1_031_826.85),
    ]
    out = apply_ocbc_amount_policy(rows)
    assert _amt(out[1], "提取", "spent") == 29450.0
    assert _amt(out[2], "提取", "spent") == 29450.0
    assert _amt(out[3], "提取", "spent") == 20.0
    assert _amt(out[1], "原幣結餘", "balance") == 1_061_296.85


def test_page30_shape_does_not_invent_from_txn_sized_balances():
    """Withdrawal amounts mis-filed as balance without B/F → leave empty, review."""
    rows = [
        _row(description="CHQ", balance=39_525.00),
        _row(description="CHQ", balance=36_425.00),
        _row(description="CHQ", balance=50_000.00),
    ]
    out = apply_ocbc_amount_policy(rows)
    for r in out:
        assert _amt(r, "提取", "spent") is None
        assert _amt(r, "存入", "received") is None
        assert r.get("_needs_review") is True


def test_page4_shape_repairs_withdrawal_misfiled_as_balance():
    """After B/F + real balance, txn-scale 'balance' moves to withdrawal when next confirms."""
    rows = [
        _row(description="B/F BALANCE", balance=1_200_000.00),
        _row(description="TRANSFER", balance=1_178_500.01, withdrawal=21_499.99),
        _row(description="CHQ", balance=6_600.00),  # mis-filed withdrawal
        _row(description="CHQ", balance=1_171_900.01),  # true next balance
    ]
    out = apply_ocbc_amount_policy(rows)
    assert _amt(out[1], "提取", "spent") == 21499.99
    assert _amt(out[2], "提取", "spent") == 6600.0
    assert _amt(out[2], "原幣結餘", "balance") is None
    assert _amt(out[3], "提取", "spent") == 6600.0
    assert _amt(out[3], "原幣結餘", "balance") == 1_171_900.01


def test_printed_amounts_not_overwritten():
    rows = [
        _row(description="B/F BALANCE", balance=1000.0),
        _row(description="CREDIT", balance=1500.0, deposit=500.0),
        _row(description="DEBIT", balance=1400.0, withdrawal=100.0),
    ]
    out = apply_ocbc_amount_policy(rows)
    assert _amt(out[1], "存入", "received") == 500.0
    assert _amt(out[2], "提取", "spent") == 100.0


def test_poison_chain_after_bf_does_not_delta_invent():
    """B/F then txn-sized balances without next confirmation → no fake delta chain."""
    rows = [
        _row(description="B/F BALANCE", balance=1_000_000.00),
        _row(description="A", balance=39_525.00),
        _row(description="B", balance=36_425.00),
        _row(description="C", balance=50_000.00),
    ]
    out = apply_ocbc_amount_policy(rows)
    for r in out[1:]:
        assert _amt(r, "提取", "spent") is None
        assert _amt(r, "存入", "received") is None
        assert r.get("_needs_review") is True

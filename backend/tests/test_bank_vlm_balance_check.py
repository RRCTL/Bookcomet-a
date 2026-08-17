"""Unit tests for bank_vlm_balance_check (cross-VLM balance checker)."""
import pytest

from app.services.bank_vlm_balance_check import (
    compare_balance_checker,
    parse_checker_payload,
    sum_primary_deposits_withdrawals,
)


def test_parse_checker_payload_aliases():
    raw = {
        "opening": 100.0,
        "final_balance": 150.0,
        "total_credits": 80.0,
        "total_debits": 30.0,
        "currency": "HKD",
    }
    d = parse_checker_payload(raw)
    assert d["opening_balance"] == 100.0
    assert d["closing_balance"] == 150.0
    assert d["total_deposits"] == 80.0
    assert d["total_withdrawals"] == 30.0
    assert d["currency"] == "HKD"


def test_compare_match_totals():
    checker = {
        "opening_balance": None,
        "closing_balance": None,
        "total_deposits": 100.0,
        "total_withdrawals": 50.0,
        "currency": None,
    }
    st, reason = compare_balance_checker(checker, 100.0, 50.0, amount_tolerance=0.02)
    assert st == "verified"
    assert "match" in reason


def test_compare_deposit_mismatch():
    checker = {
        "total_deposits": 99.0,
        "total_withdrawals": 50.0,
    }
    st, reason = compare_balance_checker(checker, 100.0, 50.0, amount_tolerance=0.02)
    assert st == "needs_review"
    assert "total_deposits_mismatch" in reason


def test_compare_within_tolerance():
    checker = {"total_deposits": 100.01, "total_withdrawals": 49.99}
    st, _ = compare_balance_checker(checker, 100.0, 50.0, amount_tolerance=0.02)
    assert st == "verified"


def test_compare_no_numeric_fields():
    checker = {
        "opening_balance": None,
        "closing_balance": None,
        "total_deposits": None,
        "total_withdrawals": None,
    }
    st, reason = compare_balance_checker(checker, 10.0, 5.0)
    assert st == "needs_review"
    assert reason == "checker_no_numeric_fields"


def test_compare_roll_forward_fail():
    checker = {
        "opening_balance": 100.0,
        "closing_balance": 200.0,
        "total_deposits": 50.0,
        "total_withdrawals": 25.0,
    }
    st, reason = compare_balance_checker(checker, 50.0, 25.0)
    assert st == "needs_review"
    assert "roll_forward_mismatch" in reason


def test_compare_roll_forward_ok():
    checker = {
        "opening_balance": 100.0,
        "closing_balance": 125.0,
        "total_deposits": 50.0,
        "total_withdrawals": 25.0,
    }
    st, reason = compare_balance_checker(checker, 50.0, 25.0)
    assert st == "verified"


def test_compare_partial_balances_only():
    checker = {
        "opening_balance": 1.0,
        "closing_balance": 2.0,
        "total_deposits": None,
        "total_withdrawals": None,
    }
    st, reason = compare_balance_checker(checker, 999.0, 999.0)
    assert st == "verified"
    assert "partial" in reason


def test_sum_primary_deposits_withdrawals():
    txns = [
        {"deposit": 10.0, "withdrawal": None},
        {"存入": "5", "提取": None},
    ]
    d, w = sum_primary_deposits_withdrawals(txns)
    assert d == pytest.approx(15.0)
    assert w == pytest.approx(0.0)

"""Opening / B-F row detection for bank statement persistence."""
from __future__ import annotations

from app.services.bank_opening_row import is_balance_forward_opening_row


def test_is_bf_scb_balance_brought_forward():
    assert is_balance_forward_opening_row({"description": "Balance Brought Forward"})
    assert is_balance_forward_opening_row({"description_raw": "BALANCE BROUGHT FORWARD"})
    assert is_balance_forward_opening_row({"備註": "balance brought forward"})


def test_is_bf_hsbc_style():
    assert is_balance_forward_opening_row({"description": "B/F BALANCE"})
    assert is_balance_forward_opening_row({"description": "BALANCE B/F"})


def test_is_bf_chinese_carry_forward():
    assert is_balance_forward_opening_row({"description": "承上結餘"})
    assert is_balance_forward_opening_row({"description": "承前結餘"})
    assert is_balance_forward_opening_row({"description": "承前轉結"})
    assert is_balance_forward_opening_row({"description": "承前餘額 BAL B/F"})
    assert is_balance_forward_opening_row({"description": "BAL B/F"})


def test_is_bf_not_plain_transaction():
    assert not is_balance_forward_opening_row({"description": "ATM WITHDRAWAL"})
    assert not is_balance_forward_opening_row({"description": ""})

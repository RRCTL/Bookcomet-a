"""BOCOM V2: prescan-synthesized 承前餘額 BAL B/F rows (HSBC-style geometry)."""
from __future__ import annotations

from app.services.bank_statement_parser import BankStatementParser


def test_bocom_v2_bf_opening_by_section_finds_balance_between_header_and_first_amount():
    sections = [
        {"y": 100.0, "header": "儲蓄存款 SAVINGS", "account_number": "000000000000001"},
    ]
    amounts = [
        {"y": 200.0, "col": "Cr", "amount": 50.0, "text": "50.00"},
    ]
    balances = [
        {"y": 150.0, "amount": 1234.56},
    ]

    def section_for_y(y: float) -> str:
        return "儲蓄存款 SAVINGS"

    def date_for_y(y: float) -> str:
        return "2023/01/25"

    def normalize_date(raw: str) -> str:
        return raw.replace("/", "-")

    out = BankStatementParser._bocom_v2_bf_opening_by_section(
        sections,
        amounts,
        balances,
        section_for_y,
        date_for_y,
        normalize_date,
        header_y=80.0,
    )
    assert "儲蓄存款 SAVINGS" in out
    row = out["儲蓄存款 SAVINGS"]
    assert row["description"] == "承前餘額 BAL B/F"
    assert row["balance"] == 1234.56
    assert row["deposit"] is None and row["withdrawal"] is None
    assert row["account_number"] == "000000000000001"
    assert row["transaction_date"] == "2023-01-25"


def test_bocom_v2_bf_skips_unknown_section_header():
    sections = [
        {"y": 50.0, "header": "OTHER SECTION", "account_number": ""},
    ]
    amounts = [{"y": 120.0, "col": "Dr", "amount": 1.0, "text": "1.00"}]
    balances = [{"y": 90.0, "amount": 99.0}]

    def section_for_y(y: float) -> str:
        return "OTHER SECTION"

    out = BankStatementParser._bocom_v2_bf_opening_by_section(
        sections,
        amounts,
        balances,
        section_for_y,
        lambda y: "",
        lambda s: s,
        header_y=40.0,
    )
    assert out == {}

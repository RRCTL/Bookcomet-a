"""Hang Seng cover / portfolio page skip heuristics."""

from app.services.bank_statement_parser import BankStatementParser


def test_hang_seng_cover_skips_when_no_activity_headers():
    text = "Hang Seng Bank\nPORTFOLIO SUMMARY\nHKD Savings 100,000.00"
    assert BankStatementParser._hang_seng_is_cover_like_portfolio_page(text) is True


def test_hang_seng_cover_not_when_activity_table_present():
    text = (
        "Hang Seng\nPORTFOLIO SUMMARY\n"
        "Date Particulars Deposit Withdrawal Balance\n"
        "01/01/2024 X 100.00\n"
    )
    assert BankStatementParser._hang_seng_is_cover_like_portfolio_page(text) is False


def test_hang_seng_zh_summary_cover():
    text = "\u6052\u751f\u9280\u884c\n\u6236\u53e3\u7e3d\u89bd\nSome balance text"
    assert BankStatementParser._hang_seng_is_cover_like_portfolio_page(text) is True

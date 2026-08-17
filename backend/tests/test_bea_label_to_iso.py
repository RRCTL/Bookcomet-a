"""BEA date label parsing for V2 merge."""

from app.services.bank_statement_parser import BankStatementParser


def test_bea_slash_dates():
    assert BankStatementParser._bea_label_to_iso("23/02/2023", None) == "2023-02-23"
    assert BankStatementParser._bea_label_to_iso("23-02-2023", None) == "2023-02-23"
    assert BankStatementParser._bea_label_to_iso("7/1/26", None) == "2026-01-07"


def test_bea_compact_ddmmmyy():
    assert BankStatementParser._bea_label_to_iso("30SEP25", None) == "2025-09-30"
    assert BankStatementParser._bea_label_to_iso("07OCT25", None) == "2025-10-07"
    assert BankStatementParser._bea_label_to_iso("31OCT2025", None) == "2025-10-31"


def test_bea_partial_month_uses_header():
    ym = (2023, 2)
    assert BankStatementParser._bea_label_to_iso("15 Jan", ym) == "2023-01-15"

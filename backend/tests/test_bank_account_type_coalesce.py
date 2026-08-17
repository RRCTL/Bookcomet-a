"""Tests for bank workflow account_type normalization and forward-fill."""
from app.services.bank_account_type_coalesce import (
    coalesce_bank_account_type_rows,
    normalize_bank_account_type_label,
)


def test_normalize_partial_hkd_statement_savings():
    assert normalize_bank_account_type_label("HKD STATEM") == "HKD STATEMENT SAVINGS"


def test_normalize_partial_hkd_current():
    assert normalize_bank_account_type_label("HKD CURRE") == "HKD CURRENT"


def test_normalize_rejects_cheque_reference_garbage():
    assert normalize_bank_account_type_label("NC1616333357(16DEC24)") == ""


def test_normalize_rejects_cheque_deposit_description():
    assert normalize_bank_account_type_label("CHEQUE DEPOSIT MACHINE") == ""


def test_coalesce_forward_fills_blank_and_garbage_rows():
    rows = [
        {"賬戶類型": "HKD STATEMENT SAVINGS", "備註": "row 1"},
        {"賬戶類型": "NC1616333357", "備註": "row 2"},
        {"賬戶類型": "", "備註": "row 3"},
        {"賬戶類型": "HKD CURRENT", "備註": "row 4"},
        {"account_type": "CHEQUE DE", "備註": "row 5"},
    ]
    out = coalesce_bank_account_type_rows(rows)
    assert out[0]["賬戶類型"] == "HKD STATEMENT SAVINGS"
    assert out[1]["賬戶類型"] == "HKD STATEMENT SAVINGS"
    assert out[2]["賬戶類型"] == "HKD STATEMENT SAVINGS"
    assert out[3]["賬戶類型"] == "HKD CURRENT"
    assert out[4]["賬戶類型"] == "HKD CURRENT"


def test_normalize_rejects_transfer_income_label():
    assert normalize_bank_account_type_label("轉帳收入") == ""
    assert normalize_bank_account_type_label("轉賬收入") == ""
    assert normalize_bank_account_type_label("CHARGES") == ""


def test_coalesce_transfer_income_rows_stay_in_savings_section():
    rows = [
        {"賬戶類型": "HKD STATEMENT SAVINGS", "備註": "before"},
        {"賬戶類型": "轉帳收入", "備註": "SAMPLE VENDOR A", "存入": "80200"},
        {"賬戶類型": "轉帳收入", "備註": "SAMPLE VENDOR B", "存入": "1015225.85"},
    ]
    out = coalesce_bank_account_type_rows(rows)
    assert out[0]["賬戶類型"] == "HKD STATEMENT SAVINGS"
    assert out[1]["賬戶類型"] == "HKD STATEMENT SAVINGS"
    assert out[2]["賬戶類型"] == "HKD STATEMENT SAVINGS"


def test_coalesce_hsbc_section_name():
    label = normalize_bank_account_type_label("HSBC Business Direct HKD Savings")
    assert label == "HKD STATEMENT SAVINGS"


def test_coalesce_resets_across_source_files():
    rows = [
        {"source_file": "HSBC-A.pdf P1", "賬戶類型": "HKD STATEMENT SAVINGS", "account_number": "111"},
        {"source_file": "HSBC-A.pdf P2", "存入": "50"},
        {"source_file": "BOC-B.pdf P1", "賬戶類型": "HKD STATEMENT SAVINGS", "account_number": "222"},
        {"source_file": "BOC-B.pdf P1", "存入": "10"},
    ]
    out = coalesce_bank_account_type_rows(rows)
    assert out[1]["account_number"] == "111"
    assert out[3]["account_number"] == "222"

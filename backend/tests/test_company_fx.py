from types import SimpleNamespace

from app.services.company_fx import (
    apply_saved_rate_to_row,
    convert_receipt_to_company,
    implied_rate_from_printed,
    company_amount_for_match,
    merge_fx_settings,
    normalize_currency_code,
    row_ready_for_books,
    same_currency,
)


def test_normalize_jyp_and_hkd_aliases():
    assert normalize_currency_code("JYP") == "JPY"
    assert normalize_currency_code("港元") == "HKD"
    assert normalize_currency_code("") == ""
    assert normalize_currency_code(None) == ""


def test_rate_and_money_quantization():
    assert float(convert_receipt_to_company(1900, 0.05)) == 95.0
    assert float(implied_rate_from_printed(1900, 94.98)) == 0.05


def test_b1_keeps_printed_company_amount():
    row = apply_saved_rate_to_row(
        {"currency": "JPY", "amount": 1900, "printed_company_amount": 94.98},
        "HKD",
        0.05,
    )
    assert row["company_amount"] == 94.98
    assert row["company_currency"] == "HKD"
    assert row["exchange_rate"] == 0.05


def test_same_currency_rate_one():
    row = apply_saved_rate_to_row({"currency": "HKD", "amount": 100, "tax_amount": 5}, "HKD", None)
    assert row["company_amount"] == 100.0
    assert row["company_tax_amount"] == 5.0
    assert row["exchange_rate"] == 1.0
    assert same_currency("港元", "HKD") is True


def test_row_ready_for_books_blocks_missing_company_amount():
    assert row_ready_for_books({"currency": "JPY", "amount": 1900}, "HKD") is False
    assert row_ready_for_books(
        {"currency": "JPY", "amount": 1900, "company_currency": "HKD", "company_amount": 94.98}
    ) is True
    assert row_ready_for_books({"currency": "HKD", "amount": 100}, "HKD") is True


def test_c1_match_uses_company_amount_not_receipt_code():
    receipt = SimpleNamespace(currency="JPY", amount=1900, company_currency="HKD", company_amount=94.98)
    bank = SimpleNamespace(currency="HKD", amount=94.98, company_currency="HKD", company_amount=94.98)
    c1, a1 = company_amount_for_match(receipt)
    c2, a2 = company_amount_for_match(bank)
    assert c1 == c2 == "HKD"
    assert a1 == a2 == 94.98
    usd_books = SimpleNamespace(currency="JPY", amount=1900, company_currency="USD", company_amount=12.0)
    assert company_amount_for_match(usd_books)[0] == "USD"


def test_merge_fx_settings_reporting_currency_empty_vs_set():
    merged = merge_fx_settings({"currency": "HKD"}, {"reporting_currency": "USD", "fx_rates": {"JPY:USD": 0.0064}})
    assert merged["reporting_currency"] == "USD"
    assert merged["fx_rates"]["JPY:USD"] == 0.0064
    cleared = merge_fx_settings(merged, {"reporting_currency": ""})
    assert cleared["reporting_currency"] is None

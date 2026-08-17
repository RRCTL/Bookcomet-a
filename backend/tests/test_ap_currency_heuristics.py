"""Tests for AP OCR-text currency hints used during AP field normalization."""
from app.api.ocr import _build_ap_multi_receipt_structured_prompt, _extract_ap_fields_from_text


def _sample_receipt(extra: str) -> str:
    """Minimal receipt-like text so _extract_ap_fields_from_text returns a row."""
    return (
        "Merchant Test Shop\n"
        "TOTAL 100.00\n"
        "01/01/2024\n"
        f"{extra}"
    )


def test_currency_hint_japanese_consumption_tax_word() -> None:
    row = _extract_ap_fields_from_text(_sample_receipt("消費税 10%"))
    assert row is not None
    assert row["currency"] == "JPY"


def test_currency_hint_japanese_tax_included() -> None:
    row = _extract_ap_fields_from_text(_sample_receipt("税込"))
    assert row is not None
    assert row["currency"] == "JPY"


def test_currency_hint_taiwan_nt_dollar() -> None:
    row = _extract_ap_fields_from_text(_sample_receipt("NT$ 500"))
    assert row is not None
    assert row["currency"] == "TWD"


def test_currency_hint_taiwan_new_dollar_word() -> None:
    row = _extract_ap_fields_from_text(_sample_receipt("新台幣"))
    assert row is not None
    assert row["currency"] == "TWD"


def test_currency_hint_euro_symbol() -> None:
    row = _extract_ap_fields_from_text(_sample_receipt("Amt € 99.00"))
    assert row is not None
    assert row["currency"] == "EUR"


def test_currency_hint_pound_symbol() -> None:
    row = _extract_ap_fields_from_text(_sample_receipt("Due £50.00"))
    assert row is not None
    assert row["currency"] == "GBP"


def test_currency_explicit_iso_takes_priority_over_euro_symbol_later() -> None:
    row = _extract_ap_fields_from_text(_sample_receipt("HKD 100.00 € ignored"))
    assert row is not None
    assert row["currency"] == "HKD"


def test_cross_verify_prompt_includes_supplement_phrase() -> None:
    p = _build_ap_multi_receipt_structured_prompt(cross_verify=True)
    assert "Verifier pass" in p
    assert "ISO 4217" in p

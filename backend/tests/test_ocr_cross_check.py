"""Tests for the independent OCR cross-check (no PaddleOCR dependency)."""

from __future__ import annotations

import app.ocr.cross_check as cc

_OCR_TEXT = "SAMPLE CAFE\nDATE 16/11/2023\nTOTAL HKD 326.70"

_GOOD_ROW = {
    "amount": "326.70",
    "currency": "HKD",
    "date": "2023-11-16",
    "payee": "SAMPLE CAFE",
}


def test_matching_row_has_no_flags() -> None:
    r = cc.cross_check_fields(_OCR_TEXT, _GOOD_ROW)
    assert r.needs_review is False
    assert r.validation_flags == ()


def test_amount_mismatch_flagged() -> None:
    r = cc.cross_check_fields(_OCR_TEXT, {**_GOOD_ROW, "amount": "999.00"})
    assert r.needs_review is True
    assert "ocr_xcheck_amount_mismatch" in r.validation_flags


def test_currency_mismatch_flagged() -> None:
    r = cc.cross_check_fields(_OCR_TEXT, {**_GOOD_ROW, "currency": "USD"})
    assert "ocr_xcheck_currency_mismatch" in r.validation_flags


def test_date_mismatch_flagged() -> None:
    r = cc.cross_check_fields(_OCR_TEXT, {**_GOOD_ROW, "date": "2020-01-01"})
    assert "ocr_xcheck_date_mismatch" in r.validation_flags


def test_merchant_mismatch_flagged() -> None:
    r = cc.cross_check_fields(_OCR_TEXT, {**_GOOD_ROW, "payee": "ACME DINER LLC"})
    assert "ocr_xcheck_merchant_mismatch" in r.validation_flags


def test_empty_ocr_text_no_flags() -> None:
    r = cc.cross_check_fields("", {**_GOOD_ROW, "amount": "999.00"})
    assert r.validation_flags == ()


def test_empty_fields_are_skipped() -> None:
    row = {"amount": "", "currency": "", "date": "", "payee": ""}
    r = cc.cross_check_fields(_OCR_TEXT, row)
    assert r.validation_flags == ()


def test_cjk_merchant_matches_by_char_overlap() -> None:
    text = "茶餐廳收據\n總金額 HKD 88.00"
    row = {"amount": "88.00", "currency": "HKD", "date": "", "payee": "茶餐廳"}
    r = cc.cross_check_fields(text, row)
    assert "ocr_xcheck_merchant_mismatch" not in r.validation_flags


def test_reader_off_by_default(monkeypatch) -> None:
    class _S:
        ap_ocr_cross_check_provider = ""
        ap_ocr_cross_check_merchant_min_overlap = 0.5

    monkeypatch.setattr(cc, "settings", _S)
    monkeypatch.setattr(cc, "_reader_cache", None)
    assert cc.get_cross_check_reader() is None


def test_unknown_provider_disabled(monkeypatch) -> None:
    class _S:
        ap_ocr_cross_check_provider = "bogus"
        ap_ocr_cross_check_merchant_min_overlap = 0.5

    monkeypatch.setattr(cc, "settings", _S)
    monkeypatch.setattr(cc, "_reader_cache", None)
    assert cc.get_cross_check_reader() is None

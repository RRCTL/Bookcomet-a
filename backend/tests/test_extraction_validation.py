"""Tests for extraction_validation helpers."""

from __future__ import annotations

from app.services import extraction_validation as ev


def test_merge_preserves_existing_flags_and_sets_needs_review() -> None:
    row = {"amount": "10", "payee": "X", "validation_flags": ["other"]}
    r = ev.ValidationResult(needs_review=True, validation_flags=("a",))
    ev.merge_validation_into_row(row, r)
    assert row["needs_review"] is True
    assert "other" in row["validation_flags"] and "a" in row["validation_flags"]


def test_validate_ar_ap_receipt_tax_math() -> None:
    norm_row = {"amount": "118.00", "payee": "Shop", "date": "2024-01-15"}
    good = {"total_amount": 118.0, "tax_amount": 18.0, "subtotal_amount": 100.0}
    assert not ev.validate_ar_ap_receipt(good, norm_row).needs_review

    bad = {"total_amount": 118.0, "tax_amount": 19.0, "subtotal_amount": 100.0}
    r = ev.validate_ar_ap_receipt(bad, norm_row)
    assert r.needs_review
    assert "tax_subtotal_total_mismatch" in r.validation_flags


def test_validate_ar_ap_suggested_tax_when_missing() -> None:
    norm_row = {"amount": "110.00", "payee": "Shop", "date": "2024-01-15"}
    obj = {"total_amount": "110", "subtotal_amount": "100", "tax_amount": None}
    r = ev.validate_ar_ap_receipt(obj, norm_row)
    assert "tax_amount_suggested_for_review" in r.validation_flags
    assert any(h[0] == "review_suggested_tax_amount" and h[1] == "10.00" for h in r.row_hints)


def test_merge_applies_review_hints() -> None:
    row = {"amount": "110.00", "payee": "Shop", "date": "2024-01-15"}
    r = ev.ValidationResult(
        needs_review=True,
        validation_flags=("tax_amount_suggested_for_review",),
        row_hints=(("review_suggested_tax_amount", "10.00"),),
    )
    ev.merge_validation_into_row(row, r)
    assert row.get("review_suggested_tax_amount") == "10.00"


def test_finalize_bank_skips_summary_row_no_flags() -> None:
    txn = {"備註": "無交易", "deposit": None, "withdrawal": None, "balance": 100}
    rows = ev.finalize_bank_transactions([txn])
    assert rows[0].get("needs_review") in (False, None)
    assert rows[0].get("validation_flags") in ([], None)


def test_batch_duplicate_ar_ap_flags_second_row() -> None:
    a = {"date": "2024-03-03", "amount": "50", "payee": "Cafe"}
    b = dict(a)
    rows = [a, b]
    ev.apply_batch_duplicate_flags_ar_ap(rows)
    assert "possible_duplicate_receipt" in rows[1].get("validation_flags", [])
    assert rows[1].get("needs_review") is True


def test_dedupe_ar_ap_rows_within_file_keeps_first_row() -> None:
    rows = [
        {"file_position": "invoice.pdf P1", "amount": "23909.68", "currency": "HKD", "payee": "Vendor", "date": "2025-04-05"},
        {"file_position": "invoice.pdf P1", "amount": "23909.68", "currency": "HKD", "date": "2025-04-05", "payee": "Vendor", "confidence": "95"},
        {"file_position": "invoice.pdf P1", "amount": "23909.68", "currency": "HKD", "payee": "Vendor", "date": "2025-04-05"},
    ]
    out = ev.dedupe_ar_ap_rows_within_file(rows)
    assert len(out) == 1
    assert "confidence" not in out[0]


def test_dedupe_ar_ap_rows_within_file_keeps_different_dates() -> None:
    rows = [
        {"file_position": "invoice.pdf P1", "amount": "23909.68", "payee": "Vendor", "date": "2025-04-05"},
        {"file_position": "invoice.pdf P1", "amount": "23909.68", "payee": "Vendor", "date": "2025-04-06"},
    ]
    assert len(ev.dedupe_ar_ap_rows_within_file(rows)) == 2


def test_dedupe_ar_ap_rows_within_file_keeps_different_amounts() -> None:
    rows = [
        {"amount": "100", "currency": "HKD", "payee": "A"},
        {"amount": "200", "currency": "HKD", "payee": "A"},
    ]
    assert len(ev.dedupe_ar_ap_rows_within_file(rows)) == 2


def test_dedupe_ar_ap_rows_within_file_per_file_payload() -> None:
    rows = [
        {"amount": "50", "currency": "HKD", "payee": "A", "date": "2024-01-01"},
        {"amount": "50", "currency": "HKD", "payee": "A", "date": "2024-01-01"},
    ]
    assert len(ev.dedupe_ar_ap_rows_within_file(rows)) == 1


def test_remove_empty_amount_rows_when_full_row_exists() -> None:
    rows = [
        {"file_position": "a.pdf P1", "amount": "100", "payee": "Shop"},
        {"file_position": "a.pdf P1", "amount": "", "payee": "Shop"},
    ]
    out = ev.remove_empty_amount_rows_per_file(rows)
    assert len(out) == 1
    assert out[0]["amount"] == "100"


def test_remove_ocr_noise_rows() -> None:
    rows = [
        {"payee": "Analysis Summary", "amount": ""},
        {"payee": "Real Vendor", "amount": "50", "date": "2024-01-01"},
    ]
    out = ev.remove_ocr_noise_rows(rows)
    assert len(out) == 1
    assert out[0]["payee"] == "Real Vendor"


def test_flag_multi_receipt_same_file() -> None:
    rows = [
        {"file_position": "a.pdf P1", "amount": "100", "payee": "Shop"},
        {"file_position": "a.pdf P1", "amount": "200", "payee": "Shop"},
    ]
    ev.flag_multi_receipt_same_file(rows)
    assert rows[0]["needs_review"] is True
    assert "multi_receipt_page" in rows[0]["validation_flags"]


def test_dedupe_ar_ap_rows_cross_file() -> None:
    rows = [
        {"amount": "100", "payee": "Shop", "date": "2024-01-01", "source_file": "a.pdf"},
        {"amount": "100", "payee": "Shop", "date": "2024-01-01", "source_file": "b.pdf"},
    ]
    out = ev.dedupe_ar_ap_rows_cross_file(
        rows,
        file_order=["file-a", "file-b"],
        row_file_ids=["file-a", "file-b"],
    )
    assert len(out) == 1
    assert out[0]["source_file"] == "a.pdf"


def test_clean_manager_ar_ap_rows() -> None:
    rows = [
        {"file_position": "a.pdf P1", "amount": "23909.68", "payee": "Vendor", "date": "2025-04-05"},
        {"file_position": "a.pdf P1", "amount": "23909.68", "payee": "Vendor", "date": "2025-04-05"},
        {"file_position": "a.pdf P1", "amount": "", "payee": "Vendor"},
        {"payee": "Analysis Summary", "amount": ""},
    ]
    out = ev.clean_manager_ar_ap_rows(rows)
    assert len(out) == 1
    assert out[0]["amount"] == "23909.68"

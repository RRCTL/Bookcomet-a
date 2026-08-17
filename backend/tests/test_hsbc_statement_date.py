"""HSBC V2 date merge: statement header (Y,M) + partial row labels."""
from __future__ import annotations

import datetime as dt

import fitz
import pytest

from app.services.bank_statement_parser import BankStatementParser


def test_partial_label_july_2022_header() -> None:
    assert BankStatementParser._hsbc_partial_label_to_iso("12 Jul", 2022, 7) == "2022-07-12"
    assert BankStatementParser._hsbc_partial_label_to_iso("11 Jul", 2022, 7) == "2022-07-11"


def test_partial_label_january_header_december_row() -> None:
    assert BankStatementParser._hsbc_partial_label_to_iso("8 Dec", 2026, 1) == "2025-12-08"


def test_partial_label_december_header() -> None:
    assert BankStatementParser._hsbc_partial_label_to_iso("1 Dec", 2025, 12) == "2025-12-01"


def test_partial_label_invalid_day_returns_empty() -> None:
    assert BankStatementParser._hsbc_partial_label_to_iso("31 Feb", 2025, 12) == ""


def test_header_year_month_top_band() -> None:
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 48), "HSBC  Page 2 of 2   21 July 2022", fontsize=12)
        ym = BankStatementParser._hsbc_header_year_month(page)
        assert ym == (2022, 7)
    finally:
        doc.close()


def test_header_year_month_last_match_wins() -> None:
    """Prefer last date in header strip (statement date often right of other text)."""
    doc = fitz.open()
    try:
        page = doc.new_page()
        page.insert_text((72, 48), "Printed 01 June 2020 Statement 21 July 2022", fontsize=10)
        ym = BankStatementParser._hsbc_header_year_month(page)
        assert ym == (2022, 7)
    finally:
        doc.close()


def test_sliding_window_uses_host_year(monkeypatch: pytest.MonkeyPatch) -> None:
    class _D(dt.date):
        @classmethod
        def today(cls) -> dt.date:
            return dt.date(2026, 4, 11)

    monkeypatch.setattr(dt, "date", _D)
    out = BankStatementParser._hsbc_label_to_date_sliding_window("15 Jul")
    assert out == "2026-07-15"


def test_merge_logic_header_overrides_host_year(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same resolution order as _hsbc_process_page_v2 merge: header (Y,M) before sliding window."""
    class _D(dt.date):
        @classmethod
        def today(cls) -> dt.date:
            return dt.date(2026, 4, 11)

    monkeypatch.setattr(dt, "date", _D)
    header_ym = (2022, 7)

    def label_to_date(label: str) -> str:
        if not label:
            return ""
        if header_ym is not None:
            y_h, m_h = header_ym
            iso = BankStatementParser._hsbc_partial_label_to_iso(label, y_h, m_h)
            if iso:
                return iso
        return BankStatementParser._hsbc_label_to_date_sliding_window(label)

    assert label_to_date("12 Jul") == "2022-07-12"

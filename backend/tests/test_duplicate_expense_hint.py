from __future__ import annotations

from app.services.duplicate_expense_hint import normalize_vendor_name


def test_normalize_vendor_name_strips_limited() -> None:
    assert normalize_vendor_name("Acme Limited").replace(" ", "") == "acme"
    assert "demo" in normalize_vendor_name("Demo Co. HK")

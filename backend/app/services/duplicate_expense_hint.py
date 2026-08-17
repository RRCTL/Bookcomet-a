"""Duplicate expense hints (rolling window + vendor normalization). Extend with DB queries when schema is wired."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session


def normalize_vendor_name(raw: str) -> str:
    s = (raw or "").strip().lower()
    for token in (" limited", " ltd", " ltd.", " company", " co.", " 有限公司"):
        s = s.replace(token, "")
    return " ".join(s.split())


def find_duplicate_expense_hints(
    db: Session,
    *,
    company_id: str,
    vendor: str,
    amount: float | None,
    txn_date: str | None,
    window_days: int = 30,
) -> list[dict[str, Any]]:
    _ = (db, company_id, vendor, amount, txn_date, window_days)
    return []

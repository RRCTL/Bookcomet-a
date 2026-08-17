"""Detect bank-statement opening / carry-forward rows (no Dr/Cr, balance-only)."""
from __future__ import annotations

from typing import Any


def is_balance_forward_opening_row(txn: dict[str, Any]) -> bool:
    """True for opening labels (SCB Balance Brought Forward, HSBC B/F, 承上結餘, …)."""
    desc = str(txn.get("description") or txn.get("description_raw") or txn.get("備註") or "")
    if not desc.strip():
        return False
    _bf_zh = (
        "\u627f\u4e0a\u7d50\u9918",  # 承上結餘
        "\u627f\u4e0a\u7ed3\u4f59",  # 承上结余
        "\u627f\u4e0a\u9918\u984d",  # 承上餘額
        "\u627f\u524d\u7d50\u9918",  # 承前結餘
        "\u627f\u524d\u7ed3\u4f59",  # 承前结余
        "\u627f\u524d\u9918\u984d",  # 承前餘額 (BOCOM)
        "\u627f\u524d\u8f49\u7d50",  # 承前轉結
        "\u627f\u524d\u8f6c\u7ed3",  # 承前转结
    )
    if any(z in desc for z in _bf_zh):
        return True
    u = desc.upper()
    return (
        "B/F BALANCE" in u
        or "BALANCE B/F" in u
        or "BALANCE BROUGHT FORWARD" in u
        or "BAL B/F" in u
        or "BAL.B/F" in u
    )

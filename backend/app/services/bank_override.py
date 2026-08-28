"""Structured bank override (Slice B) — never from filename.

When auto bank-ID fails on a scanned PDF, the client may re-submit with an
explicit ``bank_override``. This module validates the override against the
known bank registry only.
"""
from __future__ import annotations

from typing import AbstractSet


def known_bank_ids() -> AbstractSet[str]:
    try:
        from app.bank_prompts import BANK_KEYWORDS

        return set(BANK_KEYWORDS.keys()) | {"HSBC", "HANG_SENG", "DBS", "BEA", "BOC", "SCB", "BOCOM", "OCBC"}
    except Exception:
        return {"HSBC", "HANG_SENG", "DBS", "BEA", "BOC", "SCB", "BOCOM", "OCBC"}


def normalize_bank_override(raw: str | None) -> str | None:
    """Return a canonical bank id or None.

    Rejects empty / UNKNOWN. Does not inspect filenames or paths.
    """
    if raw is None:
        return None
    value = str(raw).strip().upper()
    if not value or value in {"UNKNOWN", "NONE", "NULL"}:
        return None
    # Common aliases
    aliases = {
        "HANGSENG": "HANG_SENG",
        "HANG-SENG": "HANG_SENG",
        "STANDARD_CHARTERED": "SCB",
        "STANCHARTED": "SCB",
        "BANK_OF_CHINA": "BOC",
        "BANKOFCHINA": "BOC",
    }
    value = aliases.get(value, value)
    if value in known_bank_ids():
        return value
    return None


def probe_fields_for_bank(bank_id: str) -> tuple[str, str]:
    """Return (route, adapter) for an overridden bank id."""
    if bank_id == "HSBC":
        return "hsbc_adapter", "hsbc_adapter_v2"
    return "bank_adapter", f"{bank_id.lower()}_adapter"

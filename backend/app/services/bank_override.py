"""Structured bank override (Slice B) — never from filename.

When auto bank-ID fails on a scanned PDF, the client may re-submit with an
explicit ``bank_override``. This module validates the override against the
known bank registry only.
"""
from __future__ import annotations

from typing import AbstractSet, Any


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
    # Reject filename-shaped tokens (digits, paths, extensions).
    if any(ch.isdigit() for ch in value) or "/" in value or "\\" in value or "." in value:
        return None
    # Common aliases
    aliases = {
        "HANGSENG": "HANG_SENG",
        "HANG-SENG": "HANG_SENG",
        "HANG_SENG": "HANG_SENG",
        "STANDARD_CHARTERED": "SCB",
        "STANCHARTED": "SCB",
        "BANK_OF_CHINA": "BOC",
        "BANKOFCHINA": "BOC",
    }
    value = aliases.get(value, value.replace("-", "_").replace(" ", "_"))
    if value in known_bank_ids():
        return value
    return None


def bank_override_from_graph(graph: dict[str, Any] | None) -> str | None:
    """Read explicit bank_override from workflow graph (first VLM execute path).

    Never derived from filename. Sources: top-level ``graph.bank_override`` or
    ModeConfig node data.
    """
    if not isinstance(graph, dict):
        return None
    raw = graph.get("bank_override")
    if raw is None:
        for node in graph.get("nodes") or []:
            if not isinstance(node, dict):
                continue
            if str(node.get("type") or "") != "ModeConfig":
                continue
            data = node.get("data") if isinstance(node.get("data"), dict) else {}
            raw = data.get("bank_override")
            break
    return normalize_bank_override(str(raw) if raw is not None else None)


def probe_fields_for_bank(bank_id: str) -> tuple[str, str]:
    """Return (route, adapter) for an overridden bank id."""
    if bank_id == "HSBC":
        return "hsbc_adapter", "hsbc_adapter_v2"
    return "bank_adapter", f"{bank_id.lower()}_adapter"

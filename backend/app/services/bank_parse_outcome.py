"""Explicit BANK parse outcomes (P0 performance / zero-fabrication).

Bare ``[]`` is ambiguous: no activity, provider failure, bank unknown, or
deliberate HSBC Slice-0 abstention. Callers must use ``parse_status`` /
``fallback_allowed`` — never ``if not transactions: run more VLM``.
"""
from __future__ import annotations

from typing import Any

STATUS_COMPLETED = "completed"
STATUS_NO_ACTIVITY = "no_activity"
STATUS_ABSTAINED_NEEDS_LAYOUT = "abstained_needs_layout"
STATUS_PROVIDER_FAILED = "provider_failed"
STATUS_BANK_SELECTION_REQUIRED = "bank_selection_required"

# Statuses that must never unlock generic full-page financial VLM backup.
BLOCK_GENERIC_FALLBACK = frozenset(
    {
        STATUS_ABSTAINED_NEEDS_LAYOUT,
        STATUS_PROVIDER_FAILED,
        STATUS_BANK_SELECTION_REQUIRED,
    }
)


def fallback_allowed_for_status(status: str | None) -> bool:
    if not status:
        return True
    return str(status) not in BLOCK_GENERIC_FALLBACK


def build_parse_result(
    *,
    bank: str,
    transactions: list[dict[str, Any]] | None,
    pages_processed: int,
    parse_status: str,
    full_text: str = "",
    page_verification: dict[str, str] | None = None,
    reason_codes: list[str] | None = None,
    timing_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    txns = list(transactions or [])
    status = str(parse_status or STATUS_COMPLETED)
    out: dict[str, Any] = {
        "bank": bank,
        "transactions": txns,
        "count": len(txns),
        "pages_processed": pages_processed,
        "parse_status": status,
        "fallback_allowed": fallback_allowed_for_status(status),
        "reason_codes": list(reason_codes or []),
        "ocr_preview_text": (full_text or "")[:12000],
        "ocr_preview_source": "ocr_or_pdf_text",
        "avg_transactions_per_page": (
            len(txns) / pages_processed if pages_processed > 0 else 0
        ),
        "transactions_per_page": {},
    }
    if page_verification:
        out["page_verification"] = dict(page_verification)
    if timing_summary is not None:
        out["timing_summary"] = timing_summary
    return out

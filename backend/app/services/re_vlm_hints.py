"""One-time Re-VLM correction hints (not persisted to rule memory or skills)."""

from __future__ import annotations

import re

RE_VLM_CHIP_PROMPT_LINES: dict[str, str] = {
    "missed_receipts": (
        "The previous scan missed one or more separate receipts on the page; "
        "re-segment the page and extract every distinct slip."
    ),
    "too_many_splits": "The previous scan over-segmented the page into too many receipts; merge fragments that belong to one slip.",
    "wrong_layout": "The previous scan misclassified the document type (invoice vs receipt vs cheque); re-read layout and fields accordingly.",
    "wrong_amount": "The previous scan had incorrect amount or total; re-read all monetary totals carefully.",
    "wrong_currency": "The previous scan had incorrect currency; detect ISO currency from the document.",
    "wrong_vendor": "The previous scan had incorrect vendor or payee; re-read merchant/payee names from the document.",
    "wrong_date": "The previous scan had incorrect date; re-read transaction or invoice dates.",
    "wrong_invoice_no": "The previous scan had incorrect invoice or voucher number; re-read reference numbers.",
    "gate_false_positive": "The document was previously rejected by gate but is valid; extract transactional rows.",
    "incomplete_rows": "The previous scan missed rows or columns; extract the full table or all receipt rows.",
}

RE_VLM_CHIP_IDS = frozenset(RE_VLM_CHIP_PROMPT_LINES.keys())

RE_VLM_CHIP_LABELS: dict[str, str] = {
    "missed_receipts": "Missed receipt(s) on page",
    "too_many_splits": "Too many splits",
    "wrong_layout": "Wrong document type",
    "wrong_amount": "Wrong amount",
    "wrong_currency": "Wrong currency",
    "wrong_vendor": "Wrong vendor / payee",
    "wrong_date": "Wrong date",
    "wrong_invoice_no": "Wrong invoice / voucher no.",
    "gate_false_positive": "Gate rejected valid doc",
    "incomplete_rows": "Missing rows / columns",
}

_MAX_REASONS = 8
_MAX_NOTE_LEN = 200
# Soft cap to avoid runaway splits; not a business fixed layout size.
_MAX_EXPECTED_RECEIPT_COUNT = 36

_EXPECTED_RECEIPT_COUNT_RE = re.compile(
    r"(?i)(?:\b(?:has|have|contains?|expect(?:s|ed)?|total(?:\s+of)?|all)\s+)?"
    r"(\d{1,2})\s*"
    r"(?:(?:separate|distinct|individual|different)\s+)?"
    r"(?:taxi\s+|jp(?:y)?\s+|hk(?:d)?\s+)?"
    r"(?:receipts?|slips?|vouchers?)\b"
    r"|"
    r"\b(?:receipts?|slips?|vouchers?)\s*[:=]\s*(\d{1,2})\b"
)


def validate_rescan_reasons(raw: list[str] | None) -> list[str]:
    if not raw:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        key = str(item or "").strip().lower()
        if not key or key not in RE_VLM_CHIP_IDS or key in seen:
            continue
        seen.add(key)
        out.append(key)
        if len(out) >= _MAX_REASONS:
            break
    return out


def sanitize_rescan_note(note: str | None) -> str:
    if note is None:
        return ""
    cleaned = " ".join(str(note).split())
    return cleaned[:_MAX_NOTE_LEN]


def normalize_expected_receipt_count(value: int | str | None) -> int | None:
    """Normalize an explicit expected physical-receipt count (any N in range)."""
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    if n < 2 or n > _MAX_EXPECTED_RECEIPT_COUNT:
        return None
    return n


def parse_expected_receipt_count(note: str | None) -> int | None:
    """
    Best-effort parse of an explicit receipt/slip count from free-text note.

    Examples: "Page has 9 taxi receipts", "expect 4 slips", "receipts: 3".
    Returns None when no clear count is present.
    """
    safe = sanitize_rescan_note(note)
    if not safe:
        return None
    m = _EXPECTED_RECEIPT_COUNT_RE.search(safe)
    if not m:
        return None
    return normalize_expected_receipt_count(m.group(1) or m.group(2))


def resolve_expected_receipt_count(
    *,
    explicit: int | str | None = None,
    note: str | None = None,
) -> tuple[int | None, str, str]:
    """
    Resolve expected count with source and assertion strength.

    Returns (count, source, strength) where strength is hard|soft|unknown.
    Explicit UI/API field is hard; note-parsed count is soft ranking only.
    """
    hard = normalize_expected_receipt_count(explicit)
    if hard is not None:
        return hard, "user_asserted", "hard"
    soft = parse_expected_receipt_count(note)
    if soft is not None:
        return soft, "note_parsed", "soft"
    return None, "none", "unknown"


def build_rescan_prompt_block(
    *,
    reasons: list[str] | None,
    note: str | None,
    prior_summary: str | None,
    expected_receipt_count: int | None = None,
) -> str:
    validated = validate_rescan_reasons(reasons or [])
    safe_note = sanitize_rescan_note(note)
    prior = (prior_summary or "").strip()
    expected = normalize_expected_receipt_count(expected_receipt_count)
    if not validated and not safe_note and not prior and expected is None:
        return ""

    lines = [
        "[USER RE-SCAN INSTRUCTIONS — one-time, this run only]",
        "Apply only for this re-scan. Do not treat these as permanent company rules.",
    ]
    if prior:
        lines.append(f"Previous attempt context: {prior}")
    if expected is not None:
        lines.append(
            f"Expected physical receipt count on this page: {expected}. "
            "Prefer a segmentation with that many distinct slips when visual evidence supports it; "
            "do not invent empty slips."
        )
    if validated:
        lines.append("User-selected corrections:")
        for rid in validated:
            prompt_line = RE_VLM_CHIP_PROMPT_LINES.get(rid)
            if prompt_line:
                lines.append(f"- {prompt_line}")
    if safe_note:
        lines.append(f"Additional note: {safe_note}")
    return "\n".join(lines)


def rescan_reason_labels(reasons: list[str] | None) -> list[str]:
    return [RE_VLM_CHIP_LABELS[r] for r in validate_rescan_reasons(reasons or []) if r in RE_VLM_CHIP_LABELS]

"""Detect dense bank OCR pages whose VLM extract returned no activity rows.

When OCR succeeds on a busy activity page but the LLM returns near-empty TSV,
treat that page as a soft failure (needs_retry) — not a clean success.
Never invent transactions.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Soft page status — distinct from hard "error" and clean "success".
# This is an *extract miss* on a dense page, NOT "the PDF page is blank".
EMPTY_EXTRACT_STATUS = "needs_retry"
EMPTY_EXTRACT_REASON = "dense_ocr_zero_activity"
EMPTY_EXTRACT_TITLE = "Extraction returned no rows — retry this page"
EMPTY_EXTRACT_BODY = (
    "This page had a lot of statement text, but the extract came back empty. "
    "The page is not treated as blank."
)
EMPTY_EXTRACT_RETRY_ACTION = "Retry this page"
EMPTY_EXTRACT_MARK_REVIEWED_ACTION = "Mark as reviewed with no rows"
EMPTY_EXTRACT_MARK_REVIEWED_CONFIRM = (
    "Only use this if you have checked the PDF and this page really has no "
    "transactions to extract."
)
# Neutral Live-output label after Confirm — not Passed, not amber warning.
REVIEWED_NO_ROWS_STATUS = "reviewed_no_rows"
REVIEWED_NO_ROWS_LABEL = "Reviewed — no rows"
REVIEWED_NO_ROWS_GATE = "REVIEWED_NO_ROWS"
# Back-compat alias used in logs / gate summaries (title is the user-facing string).
EMPTY_EXTRACT_UI_MESSAGE = EMPTY_EXTRACT_TITLE
EMPTY_EXTRACT_GATE = "EMPTY_EXTRACT"

# Fictional-friendly thresholds (tuned for the multi-page collapse case:
# ~170–230 OCR lines → ~94–338 char replies with zero activity rows).
DENSE_MIN_LINES = 40
DENSE_MIN_OCR_CHARS = 800
SHORT_REPLY_MAX_CHARS = 450
SHORT_REPLY_OCR_RATIO = 0.08  # reply shorter than 8% of OCR text

# Simple literal hints only — avoid nested/ambiguous regex quantifiers (CodeQL).
_ACTIVITY_HEADER_HINTS = (
    "deposit",
    "withdrawal",
    "存入",
    "提取",
    "debit",
    "credit",
    "transaction",
    "details",
    "particulars",
    "description",
    "balance",
    "結餘",
    "结余",
    "日期",
    "date",
    "bk ref",
    "voucher",
    "憑證",
)

_OPENING_DESC_HINTS = (
    "balance b/f",
    "b/f balance",
    "opening balance",
    "brought forward",
    "承上結餘",
    "承上结余",
    "上期結餘",
    "上期结余",
    "無交易",
    "无交易",
)

_AMOUNT_KEYS = (
    "存入",
    "提取",
    "deposit",
    "withdrawal",
    "received",
    "spent",
    "debit",
    "credit",
    "amount",
)

_DESC_KEYS = (
    "備註",
    "description",
    "particulars",
    "memo",
    "description_raw",
    "類型",
)


@dataclass(frozen=True)
class EmptyExtractAssessment:
    """Result of dense-OCR / empty-activity checks for one bank page."""

    is_dense: bool
    activity_row_count: int
    reply_chars: int
    ocr_chars: int
    ocr_lines: int
    short_reply: bool
    needs_retry: bool
    reason: str | None = None

    def as_page_flags(self) -> dict[str, Any]:
        if not self.needs_retry:
            return {}
        return {
            "status": EMPTY_EXTRACT_STATUS,
            "empty_extract": True,
            "empty_extract_reason": self.reason or EMPTY_EXTRACT_REASON,
            "empty_extract_title": EMPTY_EXTRACT_TITLE,
            "empty_extract_body": EMPTY_EXTRACT_BODY,
            "empty_extract_message": EMPTY_EXTRACT_TITLE,
            "empty_extract_retry_action": EMPTY_EXTRACT_RETRY_ACTION,
            "empty_extract_mark_action": EMPTY_EXTRACT_MARK_REVIEWED_ACTION,
            "empty_extract_mark_confirm": EMPTY_EXTRACT_MARK_REVIEWED_CONFIRM,
            "empty_extract_acknowledged": False,
            # True after the automatic in-pipeline retry also returned no rows.
            "empty_extract_auto_retried": False,
            "empty_extract_meta": {
                "ocr_lines": self.ocr_lines,
                "ocr_chars": self.ocr_chars,
                "reply_chars": self.reply_chars,
                "activity_row_count": self.activity_row_count,
            },
        }


def _norm(text: Any) -> str:
    return str(text or "").strip()


def _lower(text: Any) -> str:
    return _norm(text).lower()


def ocr_has_activity_table_signals(ocr_text: str) -> bool:
    """True when OCR text looks like a bank activity table (not only a cover blurb)."""
    low = _lower(ocr_text)
    if not low:
        return False
    hits = sum(1 for hint in _ACTIVITY_HEADER_HINTS if hint in low)
    return hits >= 2


def ocr_looks_dense(
    ocr_text: str,
    *,
    lines_count: int | None = None,
) -> bool:
    """Dense enough that an empty extract is suspicious (not a blank cover)."""
    text = _norm(ocr_text)
    lines = int(lines_count) if lines_count is not None else text.count("\n") + (1 if text else 0)
    dense_volume = lines >= DENSE_MIN_LINES or len(text) >= DENSE_MIN_OCR_CHARS
    if not dense_volume:
        return False
    return ocr_has_activity_table_signals(text)


def _cell_amount(row: dict[str, Any]) -> float | None:
    for key in _AMOUNT_KEYS:
        raw = row.get(key)
        if raw is None:
            continue
        text = _norm(raw).replace(",", "")
        if not text:
            continue
        try:
            return float(text)
        except ValueError:
            continue
    return None


def _row_description(row: dict[str, Any]) -> str:
    for key in _DESC_KEYS:
        text = _norm(row.get(key))
        if text:
            return text
    return ""


def _desc_is_non_activity(desc: str) -> bool:
    low = _lower(desc)
    if not low:
        return True
    return any(hint in low for hint in _OPENING_DESC_HINTS)


def is_bank_activity_row(row: dict[str, Any]) -> bool:
    """True for a transaction-like row (has amount); opening/B/F-only rows are not activity."""
    if not isinstance(row, dict):
        return False
    amount = _cell_amount(row)
    if amount is None or amount == 0:
        # Opening balances often have balance only and empty deposit/withdrawal.
        return False
    desc = _row_description(row)
    if _desc_is_non_activity(desc) and abs(amount) == 0:
        return False
    return True


def count_activity_rows(rows: list[Any] | None) -> int:
    if not isinstance(rows, list):
        return 0
    return sum(1 for row in rows if isinstance(row, dict) and is_bank_activity_row(row))


def extract_rows_from_ai_payload(ai_enhanced: Any) -> list[dict[str, Any]]:
    if not isinstance(ai_enhanced, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("tsv_rows", "transactions", "rows"):
        raw = ai_enhanced.get(key)
        if isinstance(raw, list):
            rows.extend(r for r in raw if isinstance(r, dict))
    return rows


def reply_char_count(ai_enhanced: Any, *, raw_fallback: str = "") -> int:
    if isinstance(ai_enhanced, dict):
        for key in ("raw_text", "raw_ocr_text"):
            text = ai_enhanced.get(key)
            if isinstance(text, str) and text.strip():
                # Prefer model reply over echoed OCR.
                if key == "raw_text":
                    return len(text.strip())
        # Reconstruct approximate reply length from TSV when present.
        rows = extract_rows_from_ai_payload(ai_enhanced)
        if rows:
            return max(40, len(rows) * 40)
        raw = ai_enhanced.get("raw_text")
        if isinstance(raw, str):
            return len(raw.strip())
    return len(_norm(raw_fallback))


def reply_looks_suspiciously_short(
    reply_chars: int,
    ocr_chars: int,
) -> bool:
    if reply_chars <= 0:
        return True
    if reply_chars <= SHORT_REPLY_MAX_CHARS:
        if ocr_chars <= 0:
            return True
        return reply_chars <= max(80, int(ocr_chars * SHORT_REPLY_OCR_RATIO))
    return False


def assess_bank_page_extract(
    *,
    ocr_text: str,
    lines_count: int | None = None,
    ai_enhanced: Any = None,
    raw_reply: str = "",
) -> EmptyExtractAssessment:
    """Assess whether a bank_statement_page extract should be soft-failed / retried."""
    text = _norm(ocr_text)
    lines = int(lines_count) if lines_count is not None else (
        text.count("\n") + (1 if text else 0)
    )
    dense = ocr_looks_dense(text, lines_count=lines)
    rows = extract_rows_from_ai_payload(ai_enhanced)
    activity = count_activity_rows(rows)
    reply_chars = reply_char_count(ai_enhanced, raw_fallback=raw_reply)
    short = reply_looks_suspiciously_short(reply_chars, len(text))
    # Dense OCR + zero activity rows = extract miss (not "page is blank").
    # Short reply is logged as supporting evidence but density+empty is enough.
    needs = bool(dense and activity == 0)
    reason = EMPTY_EXTRACT_REASON if needs else None
    return EmptyExtractAssessment(
        is_dense=dense,
        activity_row_count=activity,
        reply_chars=reply_chars,
        ocr_chars=len(text),
        ocr_lines=lines,
        short_reply=short,
        needs_retry=needs,
        reason=reason,
    )


def apply_empty_extract_flags(
    page: dict[str, Any],
    assessment: EmptyExtractAssessment,
    *,
    auto_retried: bool = False,
) -> dict[str, Any]:
    """Stamp or clear empty-extract flags on a page dict (mutates and returns page)."""
    if assessment.needs_retry:
        flags = assessment.as_page_flags()
        flags["empty_extract_auto_retried"] = bool(auto_retried)
        page.update(flags)
        return page
    # Successful extract — clear soft-failure flags if present.
    for key in (
        "empty_extract",
        "empty_extract_reason",
        "empty_extract_title",
        "empty_extract_body",
        "empty_extract_message",
        "empty_extract_retry_action",
        "empty_extract_mark_action",
        "empty_extract_mark_confirm",
        "empty_extract_meta",
        "empty_extract_acknowledged",
        "empty_extract_auto_retried",
    ):
        page.pop(key, None)
    if page.get("status") == EMPTY_EXTRACT_STATUS:
        page["status"] = "success"
    else:
        page.setdefault("status", "success")
    return page


def page_has_pending_empty_extract(page: Any) -> bool:
    """True when a page still blocks clean Pass / Approve (extract miss unresolved)."""
    if not isinstance(page, dict):
        return False
    if page.get("empty_extract_acknowledged") is True:
        return False
    if page.get("empty_extract") is True:
        return True
    return page.get("status") == EMPTY_EXTRACT_STATUS


def collect_pending_empty_extract_pages(pages: Any) -> list[dict[str, Any]]:
    if not isinstance(pages, list):
        return []
    return [p for p in pages if page_has_pending_empty_extract(p)]


def summarize_empty_extract_for_file(pages: Any) -> dict[str, Any] | None:
    pending = collect_pending_empty_extract_pages(pages)
    if not pending:
        return None
    page_nums = []
    for p in pending:
        try:
            page_nums.append(int(p.get("page")))
        except (TypeError, ValueError):
            continue
    page_nums = sorted({n for n in page_nums if n > 0})
    count = len(page_nums) or len(pending)
    return {
        "gate": EMPTY_EXTRACT_GATE,
        "pending_pages": page_nums,
        "count": count,
        "title": EMPTY_EXTRACT_TITLE,
        "body": EMPTY_EXTRACT_BODY,
        "message": f"{count} page(s): {EMPTY_EXTRACT_TITLE}",
    }


def page_is_reviewed_no_rows(page: Any) -> bool:
    """True when the user confirmed this page has no rows to extract."""
    if not isinstance(page, dict):
        return False
    if page.get("empty_extract_acknowledged") is True:
        return True
    return page.get("status") == REVIEWED_NO_ROWS_STATUS


def collect_reviewed_no_rows_pages(pages: Any) -> list[dict[str, Any]]:
    if not isinstance(pages, list):
        return []
    return [p for p in pages if page_is_reviewed_no_rows(p)]


def _page_num(page: dict[str, Any]) -> int | None:
    try:
        n = int(page.get("page"))
    except (TypeError, ValueError):
        return None
    return n if n >= 1 else None


def live_page_status_label(page: dict[str, Any]) -> str | None:
    """One Live-output fragment for a page (None = quiet success / not notable)."""
    n = _page_num(page)
    if n is None:
        return None
    if page_is_reviewed_no_rows(page):
        return f"Page {n}: {REVIEWED_NO_ROWS_LABEL}"
    if page_has_pending_empty_extract(page):
        return f"Page {n}: needs retry"
    status = str(page.get("status") or "").lower()
    if status == "error":
        return f"Page {n}: error"
    if status in ("running", "processing", "pending"):
        return f"Page {n}: Processing"
    return None


def format_live_output_page_statuses(
    pages: Any,
    *,
    file_status: str | None = None,
    page_count: int | None = None,
) -> str:
    """Per-page Live output: 'Page 1: Reviewed — no rows · Page 2: needs retry'."""
    dict_pages = [p for p in pages if isinstance(p, dict)] if isinstance(pages, list) else []
    parts: list[str] = []
    seen: set[int] = set()
    for page in sorted(dict_pages, key=lambda p: _page_num(p) or 0):
        label = live_page_status_label(page)
        n = _page_num(page)
        if n is not None:
            seen.add(n)
        if label:
            parts.append(label)
    # While the file is still processing, surface pages not yet present as Processing.
    if (file_status or "").lower() == "running" and page_count and page_count > 0:
        for n in range(1, int(page_count) + 1):
            if n not in seen:
                parts.append(f"Page {n}: Processing")
    return " · ".join(parts)


def summarize_reviewed_no_rows_for_file(pages: Any) -> dict[str, Any] | None:
    """Live-output summary when amber is cleared via mark-reviewed (not Passed)."""
    reviewed = collect_reviewed_no_rows_pages(pages)
    if not reviewed:
        return None
    page_nums = sorted({n for p in reviewed if (n := _page_num(p)) is not None})
    message = format_live_output_page_statuses(pages) or REVIEWED_NO_ROWS_LABEL
    return {
        "gate": REVIEWED_NO_ROWS_GATE,
        "reviewed_pages": page_nums,
        "count": len(page_nums) or len(reviewed),
        "label": REVIEWED_NO_ROWS_LABEL,
        "message": message,
    }


def preserve_user_reviewed_pages(
    existing_pages: Any,
    incoming_pages: Any,
) -> list[dict[str, Any]]:
    """Mark wins: late VLM must not overwrite acknowledged / reviewed_no_rows pages.

    Matches by 1-based page number. Reviewed pages from ``existing`` replace any
    incoming page with the same number; reviewed pages missing from incoming are
    kept. Non-reviewed incoming pages pass through unchanged.
    """
    import copy

    existing = [p for p in existing_pages if isinstance(p, dict)] if isinstance(existing_pages, list) else []
    incoming = [p for p in incoming_pages if isinstance(p, dict)] if isinstance(incoming_pages, list) else []

    reviewed_by_page: dict[int, dict[str, Any]] = {}
    for p in existing:
        if not page_is_reviewed_no_rows(p):
            continue
        n = _page_num(p)
        if n is None:
            continue
        reviewed_by_page[n] = copy.deepcopy(p)

    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for p in incoming:
        n = _page_num(p)
        if n is not None and n in seen:
            continue
        if n is not None and n in reviewed_by_page:
            out.append(reviewed_by_page[n])
            seen.add(n)
        else:
            out.append(copy.deepcopy(p))
            if n is not None:
                seen.add(n)
    for n, p in sorted(reviewed_by_page.items()):
        if n not in seen:
            out.append(p)
            seen.add(n)
    out.sort(key=lambda p: _page_num(p) or 0)
    return out


def merge_result_preserving_reviewed(
    prior: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """Merge a VLM result into prior summary without clearing user mark-reviewed pages."""
    if not isinstance(prior, dict):
        return dict(incoming)
    merged = dict(incoming)
    merged["pages"] = preserve_user_reviewed_pages(prior.get("pages"), incoming.get("pages"))
    # Keep prior reviewed summary keys if pages still include marks.
    reviewed = summarize_reviewed_no_rows_for_file(merged.get("pages"))
    if reviewed:
        merged["reviewed_no_rows_summary"] = reviewed
    else:
        merged.pop("reviewed_no_rows_summary", None)
    return merged


def mark_page_reviewed_with_no_rows(page: dict[str, Any]) -> dict[str, Any]:
    """User explicitly confirms they checked the PDF and accept no extracted rows.

    Rare secondary action — only after auto/manual retry still returns no rows.
    Does not invent transactions; journals stay draft until Approve.
    Persists as acknowledged so refresh does not revive the amber banner.
    """
    page["empty_extract_acknowledged"] = True
    page["empty_extract"] = False
    page["status"] = REVIEWED_NO_ROWS_STATUS
    page["empty_extract_title"] = EMPTY_EXTRACT_TITLE
    page["empty_extract_body"] = EMPTY_EXTRACT_BODY
    page["empty_extract_message"] = REVIEWED_NO_ROWS_LABEL
    page["empty_extract_reviewed_label"] = REVIEWED_NO_ROWS_LABEL
    return page


# Back-compat alias for earlier draft name.
mark_page_intentionally_empty = mark_page_reviewed_with_no_rows

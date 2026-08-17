"""Full-page balance / totals checker for cross-VLM (model B).

Pure helpers — no HTTP. Assumes a single-currency transaction section per page;
HK bank statements are the primary target. Model B returns summary JSON only,
not transaction rows.
"""
from __future__ import annotations

from typing import Any, Literal

from app.services.bank_vlm_cross_verify import extract_deposit_withdrawal

PageVerificationStatus = Literal["verified", "needs_review"]

BANK_BALANCE_CHECKER_PROMPT: str = """You are auditing ONE bank statement page image.

TASK: Read ONLY summary figures visible on this page — opening balance, closing balance,
total deposits (credits), total withdrawals (debits) for the transaction section.
Do NOT list individual transactions. Do NOT invent numbers.

Output ONLY a valid JSON object — no markdown, no code fences, no explanation.

Schema (use null for any field not clearly visible on the page):
{
  "opening_balance": <number or null>,
  "closing_balance": <number or null>,
  "total_deposits": <number or null>,
  "total_withdrawals": <number or null>,
  "currency": <string or null, e.g. "HKD">
}

Rules:
- Numbers must be plain JSON numbers (no commas).
- If the page has no transaction summary or balances, set all numeric fields to null.
"""


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value):  # NaN
            return None
        return float(value)
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "n/a", "-", "—"}:
        return None
    for sym in ("$", "HK$", "HKD", "USD", "￥", "¥", ","):
        text = text.replace(sym, "")
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        return None


def parse_checker_payload(raw: Any) -> dict[str, Any]:
    """Normalize VLM JSON (dict or wrapped) into canonical keys."""
    if not isinstance(raw, dict):
        return {}

    # Unwrap common one-key wrappers
    if "summary" in raw and isinstance(raw["summary"], dict):
        raw = raw["summary"]
    elif "balances" in raw and isinstance(raw["balances"], dict):
        raw = {**raw, **raw["balances"]}

    def first(*keys: str) -> float | None:
        for k in keys:
            if k in raw:
                v = _to_float(raw.get(k))
                if v is not None:
                    return v
        return None

    opening = first(
        "opening_balance",
        "opening",
        "begin_balance",
        "start_balance",
        "previous_balance",
    )
    closing = first(
        "closing_balance",
        "closing",
        "end_balance",
        "final_balance",
        "current_balance",
    )
    td = first(
        "total_deposits",
        "total_credit",
        "credits_total",
        "sum_deposits",
        "total_credits",
    )
    tw = first(
        "total_withdrawals",
        "total_debit",
        "debits_total",
        "sum_withdrawals",
        "total_debits",
    )
    cur = raw.get("currency")
    currency = str(cur).strip() if cur is not None and str(cur).strip() else None

    return {
        "opening_balance": opening,
        "closing_balance": closing,
        "total_deposits": td,
        "total_withdrawals": tw,
        "currency": currency,
    }


def sum_primary_deposits_withdrawals(txns: list[dict[str, Any]]) -> tuple[float, float]:
    """Sum deposit and withdrawal columns across primary (model A) rows."""
    s_dep = 0.0
    s_wdr = 0.0
    for t in txns:
        d, w = extract_deposit_withdrawal(t)
        s_dep += d
        s_wdr += w
    return s_dep, s_wdr


def compare_balance_checker(
    checker: dict[str, Any],
    sum_dep: float,
    sum_wdr: float,
    *,
    amount_tolerance: float = 0.02,
) -> tuple[PageVerificationStatus, str]:
    """Compare checker summary to aggregated primary totals; optional roll-forward."""
    ob = checker.get("opening_balance")
    cb = checker.get("closing_balance")
    td = checker.get("total_deposits")
    tw = checker.get("total_withdrawals")

    has_any = any(
        x is not None for x in (ob, cb, td, tw)
    )
    if not has_any:
        return "needs_review", "checker_no_numeric_fields"

    def close(a: float, b: float) -> bool:
        return abs(a - b) <= amount_tolerance

    # Require match on totals when checker provided them
    if td is not None and not close(td, sum_dep):
        return (
            "needs_review",
            f"total_deposits_mismatch checker={td:.2f} primary_sum={sum_dep:.2f}",
        )
    if tw is not None and not close(tw, sum_wdr):
        return (
            "needs_review",
            f"total_withdrawals_mismatch checker={tw:.2f} primary_sum={sum_wdr:.2f}",
        )

    if (
        ob is not None
        and cb is not None
        and td is not None
        and tw is not None
    ):
        expected_close = ob + td - tw
        if not close(expected_close, cb):
            return (
                "needs_review",
                f"roll_forward_mismatch opening+deposits-withdrawals={expected_close:.2f} "
                f"closing={cb:.2f}",
            )

    # If checker only gave opening/closing without totals, we cannot compare to row sums — pass if no contradiction flagged
    if td is None and tw is None and (ob is not None or cb is not None):
        return "verified", "checker_partial_balances_only"

    return "verified", "checker_totals_match"

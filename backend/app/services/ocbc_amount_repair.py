"""OCBC post-extract amount policy: prefer printed cells; gate balance deltas.

Do not treat balance continuity as ground truth. Only fill missing deposit/withdrawal
from balance deltas when the running-balance chain is anchored (B/F) or stays on a
plausible balance scale. Otherwise leave amounts empty and mark needs_review.
"""
from __future__ import annotations

from typing import Any

from app.services.bank_opening_row import is_balance_forward_opening_row


def _parse_amount(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"-", "—", "--", "None", "none", "N/A", "n/a"}:
        return None
    for symbol in ("$", "HK$", "HKD", "USD", "￥", "¥"):
        text = text.replace(symbol, "")
    text = text.replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _format_amount(amount: float) -> str:
    fixed = f"{amount:.2f}"
    return fixed.rstrip("0").rstrip(".")


def _group_key(txn: dict[str, Any]) -> tuple[str, str]:
    account_type = str(
        txn.get("賬戶類型")
        or txn.get("帳戶類型")
        or txn.get("account_type")
        or ""
    ).strip().lower()
    currency = str(txn.get("幣別") or txn.get("currency") or "HKD").strip().upper()
    return (account_type or "__default__", currency)


def _get_balance(txn: dict[str, Any]) -> float | None:
    return _parse_amount(
        txn.get("原幣結餘") or txn.get("balance") or txn.get("結餘") or txn.get("结余")
    )


def _get_deposit(txn: dict[str, Any]) -> float | None:
    v = _parse_amount(txn.get("存入") or txn.get("received") or txn.get("deposit"))
    return v if v is not None and v > 0 else None


def _get_withdrawal(txn: dict[str, Any]) -> float | None:
    v = _parse_amount(txn.get("提取") or txn.get("spent") or txn.get("withdrawal"))
    return v if v is not None and v > 0 else None


def _clear_amounts(txn: dict[str, Any]) -> None:
    txn["存入"] = ""
    txn["提取"] = ""
    txn["received"] = ""
    txn["spent"] = ""
    txn["deposit"] = ""
    txn["withdrawal"] = ""


def _set_deposit(txn: dict[str, Any], amount: float) -> None:
    text = _format_amount(amount)
    txn["存入"] = text
    txn["received"] = text
    txn["deposit"] = text
    txn["提取"] = ""
    txn["spent"] = ""
    txn["withdrawal"] = ""


def _set_withdrawal(txn: dict[str, Any], amount: float) -> None:
    text = _format_amount(amount)
    txn["提取"] = text
    txn["spent"] = text
    txn["withdrawal"] = text
    txn["存入"] = ""
    txn["received"] = ""
    txn["deposit"] = ""


def _clear_balance(txn: dict[str, Any]) -> None:
    txn["原幣結餘"] = ""
    txn["balance"] = ""
    txn["結餘"] = ""


def _mark_review(txn: dict[str, Any]) -> None:
    txn["_needs_review"] = True


def _plausible_balance_step(prev: float, current: float) -> bool:
    """True when current looks like a continuous running balance after prev.

    Rejects jumps that treat a txn-sized figure as the new balance (pages 4/30).
    Large real drops (>50%) are left for printed amounts or review — not invented.
    """
    if prev <= 0 or current <= 0:
        return False
    ratio = current / prev
    return 0.5 <= ratio <= 1.5


def _amount_misfiled_as_balance(
    prev: float,
    current: float,
    next_bal: float | None,
    tolerance: float,
) -> str | None:
    """Return 'withdrawal' / 'deposit' when next balance confirms a mis-filed amount."""
    if prev <= 0 or current <= 0 or next_bal is None:
        return None
    # Strong: treating current as withdrawal yields the next printed running balance.
    if abs(round(prev - current, 2) - next_bal) <= tolerance:
        return "withdrawal"
    if abs(round(prev + current, 2) - next_bal) <= tolerance:
        return "deposit"
    return None


def _peek_next_balance(
    transactions: list[dict[str, Any]],
    start: int,
    group: tuple[str, str],
) -> float | None:
    for j in range(start + 1, len(transactions)):
        if _group_key(transactions[j]) != group:
            continue
        if is_balance_forward_opening_row(transactions[j]):
            return None
        return _get_balance(transactions[j])
    return None


def apply_ocbc_amount_policy(
    transactions: list[dict[str, Any]],
    *,
    tolerance: float = 0.02,
) -> list[dict[str, Any]]:
    """Repair OCBC rows: printed amounts win; gated delta fill; no invent on poison."""
    if not transactions:
        return transactions

    trusted: dict[tuple[str, str], float] = {}
    # Delta-fill only after a B/F (or BALANCE B/F) anchor for the account group.
    anchored: set[tuple[str, str]] = set()

    for i, txn in enumerate(transactions):
        group = _group_key(txn)
        bal = _get_balance(txn)
        dep = _get_deposit(txn)
        wdr = _get_withdrawal(txn)
        is_bf = is_balance_forward_opening_row(txn)

        if is_bf:
            _clear_amounts(txn)
            if bal is not None:
                trusted[group] = bal
                anchored.add(group)
            continue

        has_printed = dep is not None or wdr is not None
        prev = trusted.get(group)

        if has_printed:
            # Never overwrite VLM-printed deposit/withdrawal.
            if prev is not None and bal is not None:
                expected = round(prev + (dep or 0.0) - (wdr or 0.0), 2)
                if abs(expected - bal) <= tolerance:
                    trusted[group] = bal
                else:
                    _mark_review(txn)
            elif group in anchored and bal is not None and prev is None:
                trusted[group] = bal
            elif group in anchored and bal is None and prev is not None:
                trusted[group] = round(prev + (dep or 0.0) - (wdr or 0.0), 2)
            continue

        # No printed amount on this row.
        if bal is None:
            _mark_review(txn)
            continue

        if prev is None or group not in anchored:
            # No B/F anchor yet — do not invent amounts from an untrusted balance.
            _mark_review(txn)
            continue

        next_bal = _peek_next_balance(transactions, i, group)
        misfiled = _amount_misfiled_as_balance(prev, bal, next_bal, tolerance)
        if misfiled == "withdrawal":
            _set_withdrawal(txn, bal)
            _clear_balance(txn)
            _mark_review(txn)
            continue
        if misfiled == "deposit":
            _set_deposit(txn, bal)
            _clear_balance(txn)
            _mark_review(txn)
            continue

        if not _plausible_balance_step(prev, bal):
            _clear_amounts(txn)
            _mark_review(txn)
            continue

        delta = round(bal - prev, 2)
        if abs(delta) <= tolerance:
            _clear_amounts(txn)
        elif delta > 0:
            _set_deposit(txn, delta)
        else:
            _set_withdrawal(txn, abs(delta))
        trusted[group] = bal

    return transactions

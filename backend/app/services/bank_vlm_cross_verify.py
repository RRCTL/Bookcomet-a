"""Cross-VLM reconciliation: align two model outputs on the same page (Strategy B).

Pure helpers — no HTTP, PDF, or OCR imports. Prefer model A rows when matched;
orphans or matching failures mark the page as needs_review.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any, Literal

PageVerificationStatus = Literal["verified", "needs_review"]


def _pick(txn: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = txn.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def _parse_amount(value: Any) -> float:
    if value is None:
        return 0.0
    text = str(value).strip()
    if not text or text in {"-", "—", "--", "None", "none", "N/A", "n/a"}:
        return 0.0
    for symbol in ("$", "HK$", "HKD", "USD", "￥", "¥"):
        text = text.replace(symbol, "")
    text = text.replace(",", "").strip()
    try:
        return float(text)
    except ValueError:
        return 0.0


def _normalize_amount_token(value: Any) -> str:
    text = str(value or "").strip()
    if text in {"", "-", "—", "--", "None", "none", "N/A", "n/a"}:
        return ""
    compact = text.replace(",", "").replace("$", "").replace("HKD", "").strip()
    if compact in {"0", "0.0", "0.00"}:
        return ""
    return text


def normalize_txn_date_key(txn: dict[str, Any]) -> str:
    """Stable YYYYMMDD key, or empty if unparseable."""
    raw = _pick(
        txn,
        [
            "date",
            "transaction_date",
            "\u4ea4\u6613\u65e5\u671f",
            "\u65e5\u671f",
            "\u5165\u8cec\u65e5\u671f",
            "\u5165\u8d26\u65e5\u671f",
            "bank_date",
        ],
    )
    if not raw:
        return ""
    text = str(raw).strip().replace("/", "-")
    _mon_map = {
        "JAN": "01",
        "FEB": "02",
        "MAR": "03",
        "APR": "04",
        "MAY": "05",
        "JUN": "06",
        "JUL": "07",
        "AUG": "08",
        "SEP": "09",
        "OCT": "10",
        "NOV": "11",
        "DEC": "12",
    }
    dm = re.match(r"^(\d{1,2})([A-Za-z]{3})(\d{2})$", text)
    if dm:
        dd, mon, yy = dm.groups()
        mm = _mon_map.get(mon.upper())
        if mm:
            text = f"20{yy}-{mm}-{int(dd):02d}"
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y%m%d")
        except ValueError:
            continue
    return ""


def extract_deposit_withdrawal(txn: dict[str, Any]) -> tuple[float, float]:
    """(deposit, withdrawal) as non-negative floats, aligned with bank_statement_parser."""
    received = _pick(
        txn,
        [
            "received",
            "deposit",
            "\u6536\u5165",
            "\u5b58\u5165",
            "\u5165\u5e33",
            "credit",
            "cr",
        ],
    )
    spent = _pick(
        txn,
        [
            "spent",
            "withdrawal",
            "\u652f\u51fa",
            "\u63d0\u53d6",
            "\u51fa\u5e33",
            "debit",
            "dr",
        ],
    )
    amount = _pick(txn, ["amount", "\u91d1\u984d", "\u91d1\u989d"])
    txn_type = _pick(txn, ["\u985e\u578b", "\u7c7b\u578b", "transaction_type"])
    description = _pick(
        txn,
        [
            "description",
            "\u6458\u8981",
            "\u4ea4\u6613\u6458\u8981",
            "\u660e\u7d30",
            "\u5907\u6ce8",
            "\u5099\u8a3b",
        ],
    )

    received = _normalize_amount_token(received)
    spent = _normalize_amount_token(spent)

    received_val = _parse_amount(received)
    spent_val = _parse_amount(spent)

    if not spent and not received and amount:
        type_hint = f"{txn_type}".lower()
        if any(
            token in type_hint
            for token in [
                "\u63d0\u53d6",
                "\u652f\u51fa",
                "\u8cbb\u7528",
                "\u8d39\u7528",
                "fee",
                "withdraw",
                "debit",
            ]
        ):
            spent = amount
        else:
            received = amount
        received_val = _parse_amount(received)
        spent_val = _parse_amount(spent)

    if received_val > 0 and spent_val > 0:
        type_hint = f"{txn_type} {description}".lower()
        if any(
            token in type_hint
            for token in [
                "\u63d0\u53d6",
                "\u652f\u51fa",
                "\u8cbb\u7528",
                "\u8d39\u7528",
                "fee",
                "withdraw",
                "debit",
            ]
        ):
            received_val = 0.0
        elif any(
            token in type_hint
            for token in [
                "\u5b58\u5165",
                "\u6536\u5165",
                "\u5b58\u6b3e",
                "deposit",
                "credit",
            ]
        ):
            spent_val = 0.0
        else:
            if received_val >= spent_val:
                spent_val = 0.0
            else:
                received_val = 0.0

    return max(0.0, received_val), max(0.0, spent_val)


def normalize_txn_key(txn: dict[str, Any]) -> tuple[str, float, float]:
    """(date_yyyymmdd, deposit, withdrawal) for alignment."""
    dk = normalize_txn_date_key(txn)
    dep, wdr = extract_deposit_withdrawal(txn)
    return dk, dep, wdr


def _amounts_close(
    a: tuple[float, float],
    b: tuple[float, float],
    tol: float,
) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def reconcile_cross_vlm(
    txns_a: list[dict[str, Any]],
    txns_b: list[dict[str, Any]],
    *,
    amount_tolerance: float = 0.02,
) -> tuple[list[dict[str, Any]], PageVerificationStatus]:
    """Merge model A (primary) with model B; prefer A for matched rows.

    Returns merged transaction list (A-order, then B-only orphans) and page status.
    """
    if not txns_a and not txns_b:
        return [], "verified"

    used_b: set[int] = set()
    matched_b_for_a: dict[int, int] = {}

    for i, txn_a in enumerate(txns_a):
        ka = normalize_txn_key(txn_a)
        best_j: int | None = None
        for j, txn_b in enumerate(txns_b):
            if j in used_b:
                continue
            kb = normalize_txn_key(txn_b)
            if ka[0] and kb[0] and ka[0] != kb[0]:
                continue
            if not ka[0] and not kb[0]:
                pass
            elif ka[0] != kb[0]:
                continue
            if not _amounts_close((ka[1], ka[2]), (kb[1], kb[2]), amount_tolerance):
                continue
            best_j = j
            break
        if best_j is not None:
            used_b.add(best_j)
            matched_b_for_a[i] = best_j

    orphans_a = [i for i in range(len(txns_a)) if i not in matched_b_for_a]
    orphans_b = [j for j in range(len(txns_b)) if j not in used_b]

    page_status: PageVerificationStatus = (
        "needs_review" if (orphans_a or orphans_b) else "verified"
    )

    out: list[dict[str, Any]] = []
    for i, txn_a in enumerate(txns_a):
        row = copy.copy(txn_a)
        if i in matched_b_for_a:
            row["verification_status"] = "verified"
        else:
            row["verification_status"] = "needs_review"
        out.append(row)

    for j in orphans_b:
        row = copy.copy(txns_b[j])
        row["verification_status"] = "needs_review"
        out.append(row)

    return out, page_status

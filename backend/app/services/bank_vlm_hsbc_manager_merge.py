"""HSBC AR manager (model B): bookkeeper snapshot + balance-only merge by row index.

The manager must not alter deposit/withdrawal; merge copies only `balance` from
aligned manager rows. No manager-only rows are appended.
"""
from __future__ import annotations

import copy
import json
from typing import Any


def _pick_first(txn: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in txn and txn[k] is not None:
            return txn[k]
    return None


def build_bookkeeper_snapshot(
    txns: list[dict[str, Any]],
    *,
    desc_max: int,
    full_if_n_rows: int,
) -> str:
    """JSON array string: idx, core fields; descriptions truncated unless few rows."""
    use_full_desc = len(txns) <= full_if_n_rows
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(txns):
        desc = _pick_first(
            t,
            ("description", "memo", "\u5099\u8a3b", "description_raw"),
        )
        desc_s = str(desc) if desc is not None else ""
        if use_full_desc:
            desc_out = desc_s
        else:
            desc_out = desc_s[:desc_max] + ("..." if len(desc_s) > desc_max else "")
        rows.append(
            {
                "idx": i,
                "transaction_date": _pick_first(
                    t,
                    ("transaction_date", "\u65e5\u671f", "date", "bank_date"),
                ),
                "deposit": _pick_first(
                    t,
                    ("deposit", "\u5b58\u5165", "received"),
                ),
                "withdrawal": _pick_first(
                    t,
                    ("withdrawal", "\u63d0\u53d6", "spent"),
                ),
                "balance": _pick_first(
                    t,
                    ("balance", "\u539f\u5e63\u7d50\u9918", "\u7d50\u9918", "\u7ed3\u4f59"),
                ),
                "description": desc_out,
                "account_type": _pick_first(
                    t,
                    (
                        "account_type",
                        "\u8cec\u6236\u985e\u578b",
                        "\u5e33\u6236\u985e\u578b",
                        "\u8d26\u6237\u7c7b\u578b",
                    ),
                ),
            }
        )
    return json.dumps(rows, ensure_ascii=False)


def _optional_num_empty(v: Any) -> bool:
    return v is None


def _amounts_close(a: Any, b: Any, tol: float) -> bool:
    try:
        fa = float(str(a).replace(",", "").strip())
        fb = float(str(b).replace(",", "").strip())
        if fa != fa or fb != fb:  # NaN
            return False
        return abs(fa - fb) <= tol
    except (TypeError, ValueError):
        return False


def _balance_from_manager_row(mg: dict[str, Any]) -> Any:
    """Use only balance-like fields from manager; ignore deposit/withdrawal."""
    return _pick_first(
        mg,
        ("balance", "\u539f\u5e63\u7d50\u9918", "\u7d50\u9918", "\u7ed3\u4f59"),
    )


def _set_balance_on_row(row: dict[str, Any], val: Any) -> None:
    row["balance"] = val
    row["\u539f\u5e63\u7d50\u9918"] = val


def merge_manager_into_bookkeeper(
    bookkeeper: list[dict[str, Any]],
    manager: list[dict[str, Any]],
    *,
    amount_tolerance: float = 0.02,
) -> tuple[list[dict[str, Any]], bool, list[bool]]:
    """Balance-only merge: same row count/order as bookkeeper; never append manager rows.

    For each index i, if bookkeeper balance is empty and manager row i has a balance,
    copy balance only. Manager deposit/withdrawal are ignored.

    Returns:
        ``out_rows`` — merged transactions
        ``misaligned`` — True when manager row count ≠ bookkeeper row count
        ``per_row_balance_ok`` — False when both primary and manager balances are            non-null but differ beyond ``amount_tolerance`` (or when misaligned).
    """
    if not bookkeeper:
        return [], False, []

    misaligned = len(manager) != len(bookkeeper)
    out_rows: list[dict[str, Any]] = []
    per_row_balance_ok: list[bool] = []

    bal_keys = (
        "balance",
        "\u539f\u5e63\u7d50\u9918",
        "\u7d50\u9918",
        "\u7ed3\u4f59",
    )

    for i, bk in enumerate(bookkeeper):
        row = copy.deepcopy(bk)
        for k in list(row.keys()):
            if k.startswith("_ar_manager"):
                del row[k]

        bv_orig = _pick_first(bk, bal_keys)
        row_ok = not misaligned
        mv_bal: Any = None
        if i < len(manager):
            mv_bal = _balance_from_manager_row(manager[i])
            if not misaligned and (
                mv_bal is not None
                and not _optional_num_empty(mv_bal)
                and bv_orig is not None
                and not _optional_num_empty(bv_orig)
            ):
                row_ok = _amounts_close(mv_bal, bv_orig, amount_tolerance)

        bv = _pick_first(row, bal_keys)
        if _optional_num_empty(bv) and mv_bal is not None and not _optional_num_empty(mv_bal):
            _set_balance_on_row(row, mv_bal)
            row["_ar_manager_amended"] = True
            row["_ar_manager_fields"] = ["balance"]

        per_row_balance_ok.append(row_ok)
        out_rows.append(row)

    return out_rows, misaligned, per_row_balance_ok

"""HSBC day-end balance policy (structured metadata only; no bank-name string match).

Layout rule: HSBC prints Balance on day-end transaction rows (and on B/F /
balance-only snapshots). Mid-day transaction rows may legitimately omit balance.

Identity for this policy is always:
  bank_id + layout_profile + parser_adapter
Never match on account_type / description / loose \"HSBC\" substrings.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, MutableMapping, Sequence

HSBC_BANK_ID = "HSBC"
HSBC_LAYOUT_PROFILE_DAY_END_BALANCE = "hsbc_day_end_balance_v1"
HSBC_PARSER_ADAPTERS = frozenset({"hsbc_adapter", "hsbc_adapter_v2"})

ROW_KIND_TRANSACTION = "transaction"
ROW_KIND_BROUGHT_FORWARD = "brought_forward"
ROW_KIND_BALANCE_SNAPSHOT = "balance_snapshot"

BalanceAbsenceClass = Literal["ok", "expected", "missing", "unresolved"]


def hsbc_identity_metadata(*, parser_adapter: str = "hsbc_adapter_v2") -> dict[str, str]:
    """Structured identity fields for HSBC day-end-balance layout rows."""
    adapter = str(parser_adapter or "").strip() or "hsbc_adapter_v2"
    return {
        "bank_id": HSBC_BANK_ID,
        "layout_profile": HSBC_LAYOUT_PROFILE_DAY_END_BALANCE,
        "parser_adapter": adapter,
    }


def uses_hsbc_day_end_balance_layout(row: Mapping[str, Any]) -> bool:
    """True only when structured bank_id/layout_profile/parser_adapter match."""
    bank_id = str(row.get("bank_id") or "").strip()
    layout = str(row.get("layout_profile") or "").strip()
    adapter = str(row.get("parser_adapter") or "").strip()
    return (
        bank_id == HSBC_BANK_ID
        and layout == HSBC_LAYOUT_PROFILE_DAY_END_BALANCE
        and adapter in HSBC_PARSER_ADAPTERS
    )


def _parse_amount(v: Any) -> float | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def has_transaction_amount(row: Mapping[str, Any]) -> bool:
    dep = _parse_amount(row.get("deposit"))
    wdw = _parse_amount(row.get("withdrawal"))
    if dep is None:
        dep = _parse_amount(row.get("存入"))
    if wdw is None:
        wdw = _parse_amount(row.get("提取"))
    has_dep = dep is not None and dep != 0
    has_wdw = wdw is not None and wdw != 0
    return has_dep or has_wdw


def has_valid_transaction_anchor(row: Mapping[str, Any]) -> bool:
    anchor = row.get("row_anchor_id") or row.get("_hsbc_row_id")
    return bool(str(anchor or "").strip()) and has_transaction_amount(row)


def balance_x_band_page_coords(
    *,
    bal_hdr_x: float | None,
    page_width: float,
    table_map: Mapping[str, Any] | None = None,
) -> tuple[float, float]:
    """Return Balance column x-band in page points (not normalized)."""
    pw = float(page_width) if page_width else 0.0
    # Prefer formal table-map normalized band when present.
    if table_map and pw > 0:
        sections = table_map.get("sections") or []
        for sec in sections:
            if not isinstance(sec, dict):
                continue
            bands = sec.get("column_bands") or {}
            bal = bands.get("balance")
            if isinstance(bal, (list, tuple)) and len(bal) >= 2:
                return float(bal[0]) * pw, float(bal[1]) * pw
    bx = float(bal_hdr_x) if bal_hdr_x is not None else (pw * 0.88 if pw else 0.0)
    # Same half-widths used by HSBC prescan Balance classification.
    return bx - 52.0, bx + 48.0


def normalize_row_anchor_y_ranges(
    anchors: Sequence[Mapping[str, Any]],
    *,
    page_height: float,
    default_half_pt: float = 10.0,
) -> dict[str, tuple[float, float]]:
    """Exclusive-ish y ranges per row-anchor from midpoints between sorted anchors."""
    items: list[tuple[str, float]] = []
    for a in anchors:
        if not isinstance(a, Mapping):
            continue
        if a.get("excluded"):
            continue
        rid = str(a.get("row_id") or a.get("row_anchor_id") or "").strip()
        if not rid:
            continue
        try:
            y = float(a.get("y"))
        except (TypeError, ValueError):
            continue
        items.append((rid, y))
    items.sort(key=lambda t: (t[1], t[0]))
    ranges: dict[str, tuple[float, float]] = {}
    n = len(items)
    ph = float(page_height) if page_height else 0.0
    for i, (rid, y) in enumerate(items):
        if i == 0:
            y_lo = max(0.0, y - default_half_pt)
        else:
            y_lo = (items[i - 1][1] + y) / 2.0
        if i == n - 1:
            y_hi = min(ph, y + default_half_pt) if ph > 0 else y + default_half_pt
        else:
            y_hi = (y + items[i + 1][1]) / 2.0
        if y_hi < y_lo:
            y_lo, y_hi = y_hi, y_lo
        ranges[rid] = (y_lo, y_hi)
    return ranges


def find_balance_in_row_anchor_range(
    *,
    y_lo: float,
    y_hi: float,
    balances: Sequence[Mapping[str, Any]],
    balance_x_lo: float,
    balance_x_hi: float,
) -> tuple[float | None, bool]:
    """Attach printed balance only via row-anchor y-range + Balance x-band.

    Returns (amount_or_None, has_balance_band_token).
    """
    cands: list[tuple[float, float]] = []  # (dy_to_mid, amount)
    mid = (float(y_lo) + float(y_hi)) / 2.0
    for b in balances:
        if not isinstance(b, Mapping):
            continue
        try:
            by = float(b["y"])
            amt = float(b["amount"])
        except (TypeError, ValueError, KeyError):
            continue
        if by < float(y_lo) or by > float(y_hi):
            continue
        bx = b.get("x")
        if bx is not None:
            try:
                bx_f = float(bx)
            except (TypeError, ValueError):
                continue
            if bx_f < float(balance_x_lo) or bx_f > float(balance_x_hi):
                continue
        # Tokens already classified into balances without x still require y-range hit;
        # x-band is enforced when x is present (prescan always supplies x after this slice).
        cands.append((abs(by - mid), amt))
    if not cands:
        return None, False
    cands.sort(key=lambda t: t[0])
    return cands[0][1], True


def annotate_date_groups(rows: Sequence[MutableMapping[str, Any]]) -> None:
    """Set date-group fields and balance_missing_expected on transaction rows in place.

    Groups by (section_id, resolved ISO date). B/F and snapshot rows are skipped.
    Unresolved dates are flagged via date_group_resolved=False (never expected-missing).
    """
    # Stable index order within each group (emit order ≈ y order).
    groups: dict[tuple[str, str], list[MutableMapping[str, Any]]] = {}
    unresolved: list[MutableMapping[str, Any]] = []

    for row in rows:
        kind = str(row.get("row_kind") or ROW_KIND_TRANSACTION)
        if kind in {ROW_KIND_BROUGHT_FORWARD, ROW_KIND_BALANCE_SNAPSHOT}:
            row.pop("balance_missing_expected", None)
            row["date_group_resolved"] = None
            row["is_date_group_day_end"] = None
            continue
        if not uses_hsbc_day_end_balance_layout(row):
            continue
        if str(row.get("備註") or row.get("description") or "").strip() == "無交易":
            continue

        sec = str(row.get("section_id") or row.get("_hsbc_section_id") or "")
        dt = str(
            row.get("transaction_date") or row.get("bank_date") or row.get("date") or ""
        ).strip()
        if not dt:
            row["date_group_resolved"] = False
            row["is_date_group_day_end"] = None
            row["date_group_id"] = None
            row.pop("balance_missing_expected", None)
            unresolved.append(row)
            continue
        key = (sec, dt)
        groups.setdefault(key, []).append(row)

    for (sec, dt), members in groups.items():
        gid = f"date:{sec}:{dt}" if sec else f"date:{dt}"
        for idx, row in enumerate(members):
            row["date_group_id"] = gid
            row["date_group_resolved"] = True
            is_last = idx == len(members) - 1
            row["is_date_group_day_end"] = is_last
            row["has_balance_band_token"] = bool(row.get("has_balance_band_token"))
            if _should_mark_balance_missing_expected(row):
                row["balance_missing_expected"] = True
            else:
                row.pop("balance_missing_expected", None)

    for row in unresolved:
        row["has_balance_band_token"] = bool(row.get("has_balance_band_token"))


def _should_mark_balance_missing_expected(row: Mapping[str, Any]) -> bool:
    """Strict gate for balance_missing_expected=true (all conditions required)."""
    if not uses_hsbc_day_end_balance_layout(row):
        return False
    if str(row.get("row_kind") or ROW_KIND_TRANSACTION) != ROW_KIND_TRANSACTION:
        return False
    if not has_valid_transaction_anchor(row):
        return False
    if row.get("has_balance_band_token") is True:
        return False
    if _parse_amount(row.get("balance") if row.get("balance") is not None else row.get("原幣結餘")) is not None:
        return False
    if row.get("date_group_resolved") is not True:
        return False
    if row.get("is_date_group_day_end") is True:
        return False
    return True


def classify_balance_absence(row: Mapping[str, Any]) -> BalanceAbsenceClass:
    """Classify a null/blank balance for validation."""
    bal = _parse_amount(row.get("balance"))
    if bal is None:
        bal = _parse_amount(row.get("原幣結餘"))
    if bal is not None:
        return "ok"

    kind = str(row.get("row_kind") or ROW_KIND_TRANSACTION)
    if kind in {ROW_KIND_BROUGHT_FORWARD, ROW_KIND_BALANCE_SNAPSHOT}:
        return "missing"

    if not uses_hsbc_day_end_balance_layout(row):
        return "missing"

    if str(row.get("備註") or row.get("description") or "").strip() == "無交易":
        return "ok"

    if row.get("date_group_resolved") is not True:
        # Unresolved date group must be explicit — never silently expected.
        return "unresolved"

    if row.get("is_date_group_day_end") is True:
        return "missing"

    if _should_mark_balance_missing_expected(row) or (
        row.get("balance_missing_expected") is True
        and has_valid_transaction_anchor(row)
        and row.get("has_balance_band_token") is not True
        and row.get("is_date_group_day_end") is not True
        and row.get("date_group_resolved") is True
    ):
        return "expected"

    return "missing"

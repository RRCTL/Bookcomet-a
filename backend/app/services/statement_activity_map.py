"""Header-aware bank activity-table mapping and field repair.

General helpers for statement layouts where a leading Currency / Cur column is
often blank on transaction rows (e.g. BEA ACCOUNT ACTIVITIES). Prefer header
identity over assuming a filled first cell. Fictional fixtures only in tests.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Mapping, MutableMapping, Sequence

# Bounded, linear patterns — no nested/ambiguous quantifiers (CodeQL ReDoS).
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_COMPACT_DATE_RE = re.compile(r"^(\d{1,2})([A-Za-z]{3})(\d{2,4})$")
_SLASH_DATE_RE = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$")
_CURRENCY_CODE_RE = re.compile(r"^[A-Za-z]{3}$")
_AMOUNT_RE = re.compile(
    r"^-?"
    r"(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d{1,2})?$"
)
_ISO_THEN_AMOUNT_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})\s+"
    r"(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)$"
)
_COMPACT_THEN_AMOUNT_RE = re.compile(
    r"^(\d{1,2}[A-Za-z]{3}\d{2,4})\s+"
    r"(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?)$"
)

_MONTH3 = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

_CURRENCY_ALIASES = {
    "HKD": "HKD",
    "HK$": "HKD",
    "HK": "HKD",
    "港元": "HKD",
    "港幣": "HKD",
    "港币": "HKD",
    "USD": "USD",
    "US$": "USD",
    "CNY": "CNY",
    "RMB": "CNY",
    "CNH": "CNY",
    "EUR": "EUR",
    "GBP": "GBP",
    "JPY": "JPY",
}

# Header token → logical role (lowercase / stripped match).
_HEADER_ALIASES: dict[str, str] = {
    "cur": "currency",
    "currency": "currency",
    "ccy": "currency",
    "貨幣": "currency",
    "币别": "currency",
    "幣別": "currency",
    "date": "date",
    "txn date": "date",
    "transaction date": "date",
    "日期": "date",
    "交易日期": "date",
    "bk ref": "bk_ref",
    "bank ref": "bk_ref",
    "ref": "bk_ref",
    "reference": "bk_ref",
    "銀行備考": "bk_ref",
    "银行备考": "bk_ref",
    "transaction details": "description",
    "particulars": "description",
    "details": "description",
    "description": "description",
    "交易項目": "description",
    "交易项目": "description",
    "交易摘要": "description",
    "deposit": "deposit",
    "deposits": "deposit",
    "credit": "deposit",
    "cr": "deposit",
    "存入": "deposit",
    "withdrawal": "withdrawal",
    "withdrawals": "withdrawal",
    "debit": "withdrawal",
    "dr": "withdrawal",
    "支出": "withdrawal",
    "balance": "balance",
    "balances": "balance",
    "結餘": "balance",
    "结余": "balance",
    "原幣結餘": "balance",
}

_OPENING_MARKERS = (
    "balance b/f",
    "b/f balance",
    "承上結餘",
    "承上结余",
    "承前結餘",
    "承前结余",
    "opening balance",
    "brought forward",
)
_TOTAL_MARKERS = (
    "total transaction amount",
    "交易總金額",
    "交易总金额",
    "no.of transaction",
    "no. of transaction",
    "交易筆數",
    "交易笔数",
)


def _norm_header_token(raw: str) -> str:
    return " ".join(str(raw or "").strip().lower().split())


def classify_header_cell(raw: str) -> str | None:
    """Return logical role for a header cell, or None if unknown."""
    token = _norm_header_token(raw)
    if not token:
        return None
    if token in _HEADER_ALIASES:
        return _HEADER_ALIASES[token]
    # Single Chinese/English keyword without spaces.
    for key, role in _HEADER_ALIASES.items():
        if key and key in token and len(key) >= 2:
            # Prefer exact-ish: avoid matching tiny fragments.
            if token == key or token.startswith(key) or token.endswith(key):
                return role
    return None


def build_header_index(headers: Sequence[str]) -> dict[str, int]:
    """Map logical role → first column index from a header row."""
    out: dict[str, int] = {}
    for i, cell in enumerate(headers):
        role = classify_header_cell(str(cell or ""))
        if role and role not in out:
            out[role] = i
    return out


def is_valid_currency_code(raw: Any) -> bool:
    """True when value is a recognised 3-letter (or alias) currency code."""
    text = str(raw or "").strip()
    if not text:
        return False
    if text in _CURRENCY_ALIASES or text.upper() in _CURRENCY_ALIASES:
        return True
    return bool(_CURRENCY_CODE_RE.match(text)) and text.upper() not in {
        # Common false positives that look like codes but are months / junk
        "AUG",
        "SEP",
        "OCT",
        "NOV",
        "DEC",
        "JAN",
        "FEB",
        "MAR",
        "APR",
        "MAY",
        "JUN",
        "JUL",
    }


def normalize_currency_token(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    if text in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[text]
    upper = text.upper()
    if upper in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[upper]
    if _CURRENCY_CODE_RE.match(text):
        return upper
    return ""


def parse_statement_date(raw: Any) -> str:
    """Parse ISO, DDMMMYY / DDMMMYYYY, or slash dates → YYYY-MM-DD (or "")."""
    text = str(raw or "").strip()
    if not text:
        return ""
    m_iso = _ISO_DATE_RE.match(text)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))).isoformat()
        except ValueError:
            return ""
    compact = text.replace(" ", "")
    m_c = _COMPACT_DATE_RE.match(compact)
    if m_c:
        try:
            day = int(m_c.group(1))
            mon = _MONTH3.get(m_c.group(2).upper(), 0)
            yr = int(m_c.group(3))
            if not mon:
                return ""
            if yr < 100:
                yr += 2000 if yr < 70 else 1900
            return date(yr, mon, day).isoformat()
        except (ValueError, OverflowError):
            return ""
    m_s = _SLASH_DATE_RE.match(compact)
    if m_s:
        try:
            day, mon, yr = int(m_s.group(1)), int(m_s.group(2)), int(m_s.group(3))
            if yr < 100:
                yr += 2000 if yr < 70 else 1900
            return date(yr, mon, day).isoformat()
        except ValueError:
            return ""
    return ""


def looks_like_statement_date(raw: Any) -> bool:
    return bool(parse_statement_date(raw))


def parse_amount_token(raw: Any) -> float | None:
    text = str(raw or "").strip()
    if not text or text in {"-", "—", "--", "None", "none", "N/A", "n/a"}:
        return None
    cleaned = text.replace("$", "").replace("HK$", "").replace(",", "").strip()
    # Strip a leading/trailing known currency code only when separated.
    for code in ("HKD", "USD", "CNY", "EUR", "GBP", "JPY"):
        if cleaned.upper().startswith(code + " "):
            cleaned = cleaned[len(code) :].strip()
        if cleaned.upper().endswith(" " + code):
            cleaned = cleaned[: -len(code)].strip()
    if not cleaned:
        return None
    if not _AMOUNT_RE.match(cleaned.replace(",", "")) and not re.match(
        r"^-?\d+(?:\.\d{1,2})?$", cleaned.replace(",", "")
    ):
        # Reject tokens that still contain letters (misplaced dates etc.).
        if re.search(r"[A-Za-z\u4e00-\u9fff]", cleaned):
            return None
    try:
        return float(cleaned.replace(",", ""))
    except ValueError:
        return None


def is_valid_amount_token(raw: Any) -> bool:
    """Non-empty amount cell must parse as a number; empty is allowed."""
    text = str(raw or "").strip()
    if not text or text in {"-", "—", "--"}:
        return True
    return parse_amount_token(text) is not None


def _cell(row: Sequence[Any], idx: int | None) -> str:
    if idx is None or idx < 0 or idx >= len(row):
        return ""
    val = row[idx]
    if val is None:
        return ""
    return str(val).strip()


def align_cells_to_headers(
    headers: Sequence[str],
    cells: Sequence[Any],
) -> list[str]:
    """Pad/shift cells when a blank leading Currency column was dropped.

    If headers start with a currency column and the first data cell looks like a
    date (not a currency code), insert an empty currency cell so Date stays in
    the Date column.
    """
    roles = [classify_header_cell(str(h or "")) for h in headers]
    values = ["" if c is None else str(c).strip() for c in cells]
    if not roles or not values:
        return values

    if roles[0] == "currency" and len(values) < len(headers):
        first = values[0]
        if first and not is_valid_currency_code(first) and looks_like_statement_date(first):
            values = [""] + values
        elif not first:
            # Already blank-leading but short — pad later.
            pass

    # Right-pad / trim to header width without inventing middle shifts.
    if len(values) < len(headers):
        values = values + [""] * (len(headers) - len(values))
    elif len(values) > len(headers):
        # Extra trailing cells: append into description when present.
        idx = build_header_index(headers)
        desc_i = idx.get("description")
        if desc_i is not None:
            extra = " ".join(values[len(headers) :])
            values = values[: len(headers)]
            if extra:
                values[desc_i] = (values[desc_i] + " " + extra).strip()
        else:
            values = values[: len(headers)]
    return values


def _desc_is_opening(desc: str) -> bool:
    d = desc.lower()
    return any(m in d or m in desc for m in _OPENING_MARKERS)


def _desc_is_total(desc: str) -> bool:
    d = desc.lower().replace(" ", "")
    raw = desc
    if "totaltransactionamount" in d:
        return True
    for m in _TOTAL_MARKERS:
        if m in raw or m.replace(" ", "") in d:
            return True
    return False


def _row_has_date(role_idx: Mapping[str, int], cells: Sequence[str]) -> bool:
    return bool(parse_statement_date(_cell(cells, role_idx.get("date"))))


def map_activity_table_rows(
    headers: Sequence[str],
    data_rows: Sequence[Sequence[Any]],
    *,
    default_currency: str = "",
) -> list[dict[str, Any]]:
    """Map a header + cell grid into normalised bank transaction dicts.

    Handles blank Cur cells, DDMMMYY dates, Bk Ref, wrapped continuation lines,
    multi-section B/F rows, and Total Transaction Amount footers.
    """
    role_idx = build_header_index(headers)
    if "date" not in role_idx and "description" not in role_idx:
        return []

    carried_currency = normalize_currency_token(default_currency)
    out: list[dict[str, Any]] = []

    for raw in data_rows:
        cells = align_cells_to_headers(headers, raw)
        cur_raw = _cell(cells, role_idx.get("currency"))
        date_raw = _cell(cells, role_idx.get("date"))
        bk_ref = _cell(cells, role_idx.get("bk_ref"))
        desc = _cell(cells, role_idx.get("description"))
        dep_raw = _cell(cells, role_idx.get("deposit"))
        wdw_raw = _cell(cells, role_idx.get("withdrawal"))
        bal_raw = _cell(cells, role_idx.get("balance"))

        # Skip pure header repeats.
        joined = " ".join(cells).lower()
        if "transaction details" in joined and "balance" in joined and "date" in joined:
            continue

        if _desc_is_total(desc) or _desc_is_total(joined):
            continue

        date_iso = parse_statement_date(date_raw)
        cur_norm = normalize_currency_token(cur_raw)
        if cur_norm:
            carried_currency = cur_norm

        # Continuation line: no date, no amounts — merge into previous description.
        has_amount = parse_amount_token(dep_raw) is not None or parse_amount_token(wdw_raw) is not None
        has_balance = parse_amount_token(bal_raw) is not None
        if not date_iso and not has_amount and not cur_norm:
            # Lone compact date on continuation (cheque value date) → append to desc.
            maybe_date_only = parse_statement_date(desc) if desc else ""
            if out and (desc or bk_ref or maybe_date_only):
                extra_bits = [p for p in (bk_ref, desc) if p]
                if extra_bits:
                    prev = out[-1]
                    prev["description"] = (str(prev.get("description") or "") + " " + " ".join(extra_bits)).strip()
                    prev["備註"] = prev["description"]
                continue
            if not desc and not bk_ref:
                continue

        if not date_iso and not has_amount and not has_balance and not _desc_is_opening(desc):
            if out and desc:
                prev = out[-1]
                prev["description"] = (str(prev.get("description") or "") + " " + desc).strip()
                prev["備註"] = prev["description"]
            continue

        deposit = parse_amount_token(dep_raw)
        withdrawal = parse_amount_token(wdw_raw)
        balance = parse_amount_token(bal_raw)

        if bk_ref and desc:
            description = f"{bk_ref} {desc}".strip()
        else:
            description = desc or bk_ref

        is_opening = _desc_is_opening(description)
        if is_opening:
            deposit = None
            withdrawal = None

        currency = cur_norm or carried_currency or ""

        row: dict[str, Any] = {
            "date": date_iso,
            "transaction_date": date_iso or None,
            "description": description,
            "deposit": deposit,
            "withdrawal": withdrawal,
            "balance": balance,
            "currency": currency,
            "reference": bk_ref,
            "幣別": currency,
            "存入": "" if deposit is None else f"{deposit:.2f}",
            "提取": "" if withdrawal is None else f"{withdrawal:.2f}",
            "原幣結餘": "" if balance is None else f"{balance:.2f}",
            "日期": date_iso,
            "備註": description,
            "憑證號": bk_ref,
        }
        if is_opening:
            row["_opening_balance"] = True
        out.append(row)

    return out


def repair_shifted_bank_fields(txn: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Repair rows where a date (or date+amount) landed in the currency field.

    Also normalises DDMMMYY dates and clears non-currency tokens from 幣別/currency.
    """
    date_keys = ("date", "transaction_date", "日期", "bank_date")
    cur_keys = ("currency", "幣別", "币别")

    def _get_first(keys: Sequence[str]) -> str:
        for k in keys:
            v = txn.get(k)
            if v is not None and str(v).strip() != "":
                return str(v).strip()
        return ""

    date_raw = _get_first(date_keys)
    cur_raw = _get_first(cur_keys)

    date_iso = parse_statement_date(date_raw) if date_raw else ""
    if date_raw and not date_iso:
        # Leave unparseable dates for validation; don't invent.
        date_iso = ""

    repaired_cur = ""
    if cur_raw:
        if is_valid_currency_code(cur_raw):
            repaired_cur = normalize_currency_token(cur_raw)
        else:
            m_iso_amt = _ISO_THEN_AMOUNT_RE.match(cur_raw)
            m_c_amt = _COMPACT_THEN_AMOUNT_RE.match(cur_raw)
            if m_iso_amt:
                if not date_iso:
                    date_iso = parse_statement_date(m_iso_amt.group(1))
                # Amount jammed into currency — prefer empty deposit/withdrawal fill.
                amt = parse_amount_token(m_iso_amt.group(2))
                if amt is not None:
                    _fill_missing_amount(txn, amt)
            elif m_c_amt:
                if not date_iso:
                    date_iso = parse_statement_date(m_c_amt.group(1))
                amt = parse_amount_token(m_c_amt.group(2))
                if amt is not None:
                    _fill_missing_amount(txn, amt)
            elif looks_like_statement_date(cur_raw):
                if not date_iso:
                    date_iso = parse_statement_date(cur_raw)
            # else: drop invalid currency token
            repaired_cur = ""

    if date_iso:
        for k in date_keys:
            if k in txn or k in ("date", "日期"):
                txn[k] = date_iso
        txn["date"] = date_iso
        txn["日期"] = date_iso
        if "transaction_date" in txn or txn.get("transaction_date") is None:
            txn["transaction_date"] = date_iso

    # Write currency mirrors; keep empty when unknown (caller may default).
    for k in cur_keys:
        if k in txn or k in ("currency", "幣別"):
            txn[k] = repaired_cur
    txn["currency"] = repaired_cur
    txn["幣別"] = repaired_cur

    return txn


def _fill_missing_amount(txn: MutableMapping[str, Any], amt: float) -> None:
    """If both deposit and withdrawal empty, put amount into withdrawal as safer default.

    Prefer not to invent side; only fill when both sides are blank so a jammed
    currency token's amount is not lost for review.
    """
    dep = txn.get("deposit", txn.get("存入", txn.get("received")))
    wdw = txn.get("withdrawal", txn.get("提取", txn.get("spent")))
    dep_n = parse_amount_token(dep)
    wdw_n = parse_amount_token(wdw)
    if dep_n is not None or wdw_n is not None:
        return
    # Prefer leaving for human review via balance column if present.
    bal = parse_amount_token(txn.get("balance", txn.get("原幣結餘")))
    if bal is None:
        txn["withdrawal"] = amt
        txn["提取"] = f"{amt:.2f}"
        txn["spent"] = f"{amt:.2f}"


def bank_row_placement_issues(row: Mapping[str, Any]) -> list[str]:
    """Return issue codes when date/amount/currency look misplaced or invalid.

    Used by review-grid UX and resume/approve rejection. Empty list = OK.
    """
    desc = str(row.get("備註") or row.get("description") or row.get("particulars") or "").strip()
    if desc == "無交易":
        return []

    issues: list[str] = []

    date_raw = str(
        row.get("date")
        or row.get("transaction_date")
        or row.get("日期")
        or row.get("bank_date")
        or ""
    ).strip()
    date_iso = parse_statement_date(date_raw) if date_raw else ""
    if not date_raw or not date_iso:
        # Opening B/F without a printed date is rare; still flag for review.
        issues.append("bank_date_missing_or_unparseable")
    elif date_raw and not date_iso:
        issues.append("bank_date_missing_or_unparseable")

    cur_raw = str(row.get("currency") or row.get("幣別") or row.get("币别") or "").strip()
    if cur_raw and not is_valid_currency_code(cur_raw):
        issues.append("bank_currency_misplaced")

    for key, flag in (
        ("deposit", "bank_amount_misplaced"),
        ("存入", "bank_amount_misplaced"),
        ("received", "bank_amount_misplaced"),
        ("withdrawal", "bank_amount_misplaced"),
        ("提取", "bank_amount_misplaced"),
        ("spent", "bank_amount_misplaced"),
        ("balance", "bank_amount_misplaced"),
        ("原幣結餘", "bank_amount_misplaced"),
    ):
        if key not in row:
            continue
        raw = row.get(key)
        if raw is None:
            continue
        text = str(raw).strip()
        if not text or text in {"-", "—", "--"}:
            continue
        if not is_valid_amount_token(text):
            if flag not in issues:
                issues.append(flag)

    return issues


def bank_row_has_placement_issues(row: Mapping[str, Any]) -> bool:
    return bool(bank_row_placement_issues(row))

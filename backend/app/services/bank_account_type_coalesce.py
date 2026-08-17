"""Normalize and forward-fill bank statement account_type / 賬戶類型 on workflow TSV rows."""
from __future__ import annotations

import re
from typing import Any

from app.services.bank_statement_parser import BankStatementParser

_ACCOUNT_TYPE_KEYS = ("賬戶類型", "帳戶類型", "账户类型", "account_type")
_CANONICAL = frozenset({"HKD CURRENT", "HKD STATEMENT SAVINGS", "FCY SAVINGS", "CASH"})
_REF_LIKE = re.compile(r"^(NC\d+|HC\d+|[A-Z]{2,3}\d{5,})", re.IGNORECASE)

# Transaction detail labels that VLM sometimes misplaces into 賬戶類型.
_TRANSACTION_TYPE_LABELS = frozenset(
    {
        "轉帳收入",
        "轉賬收入",
        "轉帳支出",
        "轉賬支出",
        "利息收入",
        "利息支出",
        "CHARGES",
        "CREDIT INTEREST",
        "NET BILPYT",
        "WITHDRAWL",
        "WITHDRAWAL",
        "SAL",
        "ITEM(S)",
        "ITEM(S) AMOUNT",
    }
)

_ACCOUNT_HEADER_HINTS = (
    "SAVINGS",
    "CURRENT",
    "STATEMENT",
    "ACCOUNT",
    "HKD",
    "FCY",
    "CASH",
    "BUSINESS DIRECT",
    "SPRINT",
    "儲蓄",
    "往來",
    "外幣",
    "港元",
    "支票",
)


def _pick_account_type(row: dict[str, Any]) -> str:
    for key in _ACCOUNT_TYPE_KEYS:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _is_transaction_type_label(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if text in _TRANSACTION_TYPE_LABELS:
        return True
    upper = text.upper()
    if upper in _TRANSACTION_TYPE_LABELS:
        return True
    for label in ("轉帳收入", "轉賬收入", "轉帳支出", "轉賬支出", "利息收入", "利息支出"):
        if text == label:
            return True
    return False


def _looks_like_account_section_header(raw: str) -> bool:
    text = (raw or "").strip()
    if not text or _is_transaction_type_label(text):
        return False
    upper = text.upper()
    return any(hint in upper or hint in text for hint in _ACCOUNT_HEADER_HINTS)


def _looks_like_garbage_account_type(raw: str) -> bool:
    text = (raw or "").strip()
    if not text:
        return False
    if _is_transaction_type_label(text):
        return True
    upper = text.upper()
    if _REF_LIKE.match(text):
        return True
    if re.match(r"^\d", text):
        return True
    if "CHEQUE DEPOSIT" in upper or upper.startswith("CHEQUE DE"):
        return True
    if len(text) > 35 and not _looks_like_account_section_header(text):
        return True
    return False


def normalize_bank_account_type_label(raw: str) -> str:
    """Map OCR/LLM fragments to stable section labels used by BankStatementReview."""
    text = (raw or "").strip()
    if not text or _looks_like_garbage_account_type(text):
        return ""
    if text in _CANONICAL:
        return text

    upper = text.upper()
    if any(token in upper for token in ("SAVINGS", "CURRENT", "STATEMENT", "HKD")) or any(
        token in text for token in ("儲蓄", "往來", "外幣")
    ):
        mapped = BankStatementParser._bea_normalise_account_header(text)
        if mapped in _CANONICAL:
            return mapped

    if "HSBC" in upper and "SAVINGS" in upper:
        return "HKD STATEMENT SAVINGS"
    if "HSBC" in upper and "CURRENT" in upper:
        return "HKD CURRENT"
    if "FOREIGN CURRENCY" in upper or "外幣" in text:
        return "FCY SAVINGS"
    if upper.startswith("HKD STATEM") or "STATEMENT SAVINGS" in upper:
        return "HKD STATEMENT SAVINGS"
    if upper.startswith("HKD CURRE") or (upper.startswith("HKD") and "CURRENT" in upper):
        return "HKD CURRENT"
    if "儲蓄" in text or "SAVINGS" in upper or "STMT" in upper:
        return "HKD STATEMENT SAVINGS"
    if "往來" in text or "CURRENT" in upper or upper in ("CHQ", "CHEQUE"):
        return "HKD CURRENT"
    if upper == "CASH" or upper.startswith("CASH"):
        return "CASH"
    return text if _looks_like_account_section_header(text) else ""


def _pick_account_number(row: dict[str, Any]) -> str:
    value = str(row.get("account_number") or "").strip()
    return value


def _source_file_stem(row: dict[str, Any]) -> str:
    text = str(row.get("source_file") or "").strip()
    return re.sub(r" P\d+\b", "", text, flags=re.IGNORECASE)


def coalesce_bank_account_type_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Forward-fill account_type and account_number; reset at each new source file."""
    last_label = ""
    last_account_number = ""
    last_source_stem = ""
    for row in rows:
        stem = _source_file_stem(row)
        if stem != last_source_stem:
            last_label = ""
            last_account_number = ""
            last_source_stem = stem
        normalized = normalize_bank_account_type_label(_pick_account_type(row))
        account_number = _pick_account_number(row)
        if normalized:
            last_label = normalized
        elif last_label:
            normalized = last_label
        if account_number:
            last_account_number = account_number
        elif last_account_number:
            account_number = last_account_number
        if normalized:
            row["account_type"] = normalized
            row["賬戶類型"] = normalized
            row["帳戶類型"] = normalized
        if account_number:
            row["account_number"] = account_number
    return rows

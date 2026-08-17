from __future__ import annotations

import uuid
import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

_DEFAULT_CHART_OF_ACCOUNTS: list[dict[str, Any]] = [
    # ── Assets (1xxx) ── Financial Position ────────────────────────────────────
    {"code": "1010", "name_en": "Cash at Bank",              "name_zh": "銀行存款",        "category_type": "asset",         "allowed_modes": ["BANK"]},
    {"code": "1020", "name_en": "Petty Cash",                "name_zh": "現金",            "category_type": "asset",         "allowed_modes": ["BANK"]},
    {"code": "1100", "name_en": "Accounts Receivable",       "name_zh": "應收帳款",        "category_type": "asset",         "allowed_modes": ["AR"]},
    {"code": "1110", "name_en": "Trade Debtors",             "name_zh": "應收貿易帳款",    "category_type": "asset",         "allowed_modes": ["AR"]},
    {"code": "1200", "name_en": "Inventory",                 "name_zh": "存貨",            "category_type": "asset",         "allowed_modes": ["AP"]},
    {"code": "1300", "name_en": "Prepaid Expenses",          "name_zh": "預付費用",        "category_type": "asset",         "allowed_modes": ["AP"]},
    {"code": "1400", "name_en": "Property, Plant & Equipment", "name_zh": "物業機器設備",  "category_type": "asset",         "allowed_modes": ["AP"]},
    {"code": "1500", "name_en": "Other Current Assets",      "name_zh": "其他流動資產",    "category_type": "asset",         "allowed_modes": ["AR"]},
    {"code": "1999", "name_en": "Reconciliation Suspense",   "name_zh": "對賬暫記",        "category_type": "asset",         "allowed_modes": ["AR", "AP", "BANK"]},
    # ── Liabilities (2xxx) ── Financial Position ────────────────────────────────
    {"code": "2100", "name_en": "Accounts Payable",          "name_zh": "應付帳款",        "category_type": "liability",     "allowed_modes": ["AP"]},
    {"code": "2200", "name_en": "Accrued Liabilities",       "name_zh": "應計負債",        "category_type": "liability",     "allowed_modes": ["AP"]},
    {"code": "2300", "name_en": "Tax Payable",               "name_zh": "應付稅款",        "category_type": "liability",     "allowed_modes": ["AP"]},
    {"code": "2400", "name_en": "Bank Loan",                 "name_zh": "銀行貸款",        "category_type": "liability",     "allowed_modes": ["BANK"]},
    {"code": "2500", "name_en": "Other Current Liabilities", "name_zh": "其他流動負債",    "category_type": "liability",     "allowed_modes": ["AP"]},
    # ── Equity (3xxx) ── Financial Position ────────────────────────────────────
    {"code": "3100", "name_en": "Share Capital",             "name_zh": "股本",            "category_type": "equity",        "allowed_modes": ["BANK"]},
    {"code": "3200", "name_en": "Retained Earnings",         "name_zh": "留存收益",        "category_type": "equity",        "allowed_modes": ["AR"]},
    {"code": "3300", "name_en": "Owner's Drawing",           "name_zh": "業主提款",        "category_type": "equity",        "allowed_modes": ["AP"]},
    # ── Revenue (4xxx) ── Income Statement ─────────────────────────────────────
    {"code": "4010", "name_en": "Sales",                     "name_zh": "銷售收入",        "category_type": "revenue",       "allowed_modes": ["AR"]},
    {"code": "4020", "name_en": "Service Income",            "name_zh": "服務收入",        "category_type": "revenue",       "allowed_modes": ["AR"]},
    {"code": "4030", "name_en": "Interest Received",         "name_zh": "利息收入",        "category_type": "revenue",       "allowed_modes": ["AR", "BANK"]},
    {"code": "4040", "name_en": "Rent Income",               "name_zh": "租金收入",        "category_type": "revenue",       "allowed_modes": ["AR"]},
    {"code": "4050", "name_en": "Other Income",              "name_zh": "其他收入",        "category_type": "other_income",  "allowed_modes": ["AR"]},
    # ── Expenses (5xxx) ── Income Statement ─────────────────────────────────────
    {"code": "5010", "name_en": "Rent",                      "name_zh": "租金支出",        "category_type": "expense",       "allowed_modes": ["AP"]},
    {"code": "5020", "name_en": "Utilities",                 "name_zh": "水電煤",          "category_type": "expense",       "allowed_modes": ["AP"]},
    {"code": "5030", "name_en": "Office Supplies",           "name_zh": "辦公用品",        "category_type": "expense",       "allowed_modes": ["AP"]},
    {"code": "5040", "name_en": "Professional Fees",         "name_zh": "專業費用",        "category_type": "expense",       "allowed_modes": ["AP"]},
    {"code": "5050", "name_en": "Insurance",                 "name_zh": "保險費用",        "category_type": "expense",       "allowed_modes": ["AP"]},
    {"code": "5060", "name_en": "Travel & Entertainment",    "name_zh": "差旅及招待",      "category_type": "expense",       "allowed_modes": ["AP"]},
    {"code": "5070", "name_en": "Advertising & Marketing",   "name_zh": "廣告及推廣",      "category_type": "expense",       "allowed_modes": ["AP"]},
    {"code": "5080", "name_en": "Bank Fee",                  "name_zh": "銀行手續費",      "category_type": "bank_fee",      "allowed_modes": ["AP", "BANK"]},
    {"code": "5090", "name_en": "Interest Paid",             "name_zh": "利息支出",        "category_type": "interest_paid", "allowed_modes": ["AP", "BANK"]},
    {"code": "5100", "name_en": "Purchases / COGS",          "name_zh": "購貨 / 銷貨成本", "category_type": "cogs",          "allowed_modes": ["AP"]},
    {"code": "5110", "name_en": "Other Expense",             "name_zh": "其他支出",        "category_type": "expense",       "allowed_modes": ["AP"]},
]


def _to_dict(entry: Any) -> dict[str, Any]:
    return {
        "id": entry.id,
        "code": entry.code,
        "name_en": entry.name_en,
        "name_zh": entry.name_zh or "",
        "category_type": entry.category_type,
        "allowed_modes": entry.allowed_modes or [],
        "is_default": bool(entry.is_default),
        "opening_balance": entry.opening_balance,
        "opening_balance_dr_cr": entry.opening_balance_dr_cr,
    }


_DEFAULT_MODES_BY_CODE: dict[str, list[str]] = {
    item["code"]: item["allowed_modes"] for item in _DEFAULT_CHART_OF_ACCOUNTS
}


def _seed_defaults(db: Session, company_id: str = "default") -> None:
    """Insert missing built-in defaults and correct allowed_modes drift on existing default accounts."""
    from app.models.reconciliation import ChartOfAccountEntry
    existing = (
        db.query(ChartOfAccountEntry)
        .filter(ChartOfAccountEntry.company_id == company_id)
        .all()
    )
    existing_by_code = {e.code: e for e in existing}

    # 1. Fix allowed_modes on existing default accounts that have drifted
    fixed = 0
    for code, correct_modes in _DEFAULT_MODES_BY_CODE.items():
        entry = existing_by_code.get(code)
        if entry and entry.is_default and entry.allowed_modes != correct_modes:
            entry.allowed_modes = correct_modes
            fixed += 1

    # 2. Add any missing default accounts
    to_add = [item for item in _DEFAULT_CHART_OF_ACCOUNTS if item["code"] not in existing_by_code]
    for item in to_add:
        db.add(ChartOfAccountEntry(
            id=str(uuid.uuid4()),
            company_id=company_id,
            code=item["code"],
            name_en=item["name_en"],
            name_zh=item["name_zh"],
            category_type=item["category_type"],
            allowed_modes=item["allowed_modes"],
            opening_balance=item.get("opening_balance"),
            opening_balance_dr_cr=item.get("opening_balance_dr_cr"),
            is_default=True,
        ))

    if fixed or to_add:
        db.commit()
    if fixed:
        logger.info("[CoA] Corrected allowed_modes on %d default accounts for company=%s", fixed, company_id)
    if to_add:
        logger.info("[CoA] Seeded %d new default accounts for company=%s", len(to_add), company_id)


# ---------------------------------------------------------------------------
# Public read functions (keep the old signature for callers that don't have a DB session)
# ---------------------------------------------------------------------------

def get_chart_of_accounts(mode: str | None = None, db: Session | None = None, company_id: str = "default") -> list[dict[str, Any]]:
    if db is None:
        # Fallback for callers (e.g. prompt builders) that don't have a session
        normalized = (mode or "").strip().upper()
        # RECON drafts span BANK/AR/AP/suspense — no account is tagged allowed_modes=["RECON"].
        if not normalized or normalized == "RECON":
            return list(_DEFAULT_CHART_OF_ACCOUNTS)
        return [a for a in _DEFAULT_CHART_OF_ACCOUNTS if normalized in a.get("allowed_modes", [])]

    from app.models.reconciliation import ChartOfAccountEntry
    _seed_defaults(db, company_id)
    q = db.query(ChartOfAccountEntry).filter(ChartOfAccountEntry.company_id == company_id)
    normalized = (mode or "").strip().upper()
    results = q.all()
    entries = [_to_dict(e) for e in results]
    if normalized and normalized != "RECON":
        entries = [e for e in entries if normalized in e.get("allowed_modes", [])]
    return entries


def get_prompt_account_lines(mode: str, db: Session | None = None, company_id: str = "default") -> list[str]:
    accounts = get_chart_of_accounts(mode, db=db, company_id=company_id)
    return [f'- {a["code"]} {a["name_en"]} / {a["name_zh"]}' for a in accounts]


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def create_account(
    db: Session,
    code: str,
    name_en: str,
    name_zh: str,
    category_type: str,
    allowed_modes: list[str],
    opening_balance: float | None = None,
    opening_balance_dr_cr: str | None = None,
    company_id: str = "default",
) -> dict[str, Any]:
    from app.models.reconciliation import ChartOfAccountEntry
    _seed_defaults(db, company_id)
    # Reject duplicate code within same company
    existing = db.query(ChartOfAccountEntry).filter(
        ChartOfAccountEntry.company_id == company_id,
        ChartOfAccountEntry.code == code,
    ).first()
    if existing:
        raise ValueError(f"Account code '{code}' already exists.")
    entry = ChartOfAccountEntry(
        id=str(uuid.uuid4()),
        company_id=company_id,
        code=code.strip(),
        name_en=name_en.strip(),
        name_zh=(name_zh or "").strip(),
        category_type=category_type.strip(),
        allowed_modes=[m.upper() for m in (allowed_modes or [])],
        opening_balance=opening_balance,
        opening_balance_dr_cr=opening_balance_dr_cr,
        is_default=False,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return _to_dict(entry)


def update_account(
    db: Session,
    code: str,
    name_en: str | None = None,
    name_zh: str | None = None,
    category_type: str | None = None,
    allowed_modes: list[str] | None = None,
    opening_balance: float | None = None,
    opening_balance_dr_cr: str | None = None,
    _clear_opening_balance: bool = False,
    company_id: str = "default",
) -> dict[str, Any]:
    from app.models.reconciliation import ChartOfAccountEntry
    entry = db.query(ChartOfAccountEntry).filter(
        ChartOfAccountEntry.company_id == company_id,
        ChartOfAccountEntry.code == code,
    ).first()
    if not entry:
        raise ValueError(f"Account code '{code}' not found.")
    if name_en is not None:
        entry.name_en = name_en.strip()
    if name_zh is not None:
        entry.name_zh = name_zh.strip()
    if category_type is not None:
        entry.category_type = category_type.strip()
    if allowed_modes is not None:
        entry.allowed_modes = [m.upper() for m in allowed_modes]
    if _clear_opening_balance:
        entry.opening_balance = None
        entry.opening_balance_dr_cr = None
    else:
        if opening_balance is not None:
            entry.opening_balance = opening_balance
        if opening_balance_dr_cr is not None:
            entry.opening_balance_dr_cr = opening_balance_dr_cr
    db.commit()
    db.refresh(entry)
    return _to_dict(entry)


def delete_account(
    db: Session,
    code: str,
    referenced_codes: list[str] | None = None,
    company_id: str = "default",
) -> None:
    """
    Delete a CoA entry.
    - Raises if the entry is a built-in default.
    - Raises if the code appears in referenced_codes (passed from the frontend based on current transactions).
    """
    from app.models.reconciliation import ChartOfAccountEntry
    entry = db.query(ChartOfAccountEntry).filter(
        ChartOfAccountEntry.company_id == company_id,
        ChartOfAccountEntry.code == code,
    ).first()
    if not entry:
        raise ValueError(f"Account code '{code}' not found.")
    if entry.is_default:
        raise ValueError(f"Cannot delete built-in account '{code}'. You may edit it instead.")
    if referenced_codes and code in referenced_codes:
        raise ValueError(f"Account '{code}' is referenced by existing transactions and cannot be deleted.")
    db.delete(entry)
    db.commit()

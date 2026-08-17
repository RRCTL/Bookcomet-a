"""Chart of accounts, OCR deploy, ledger import, transaction lists, and bulk category updates."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user, get_trace_id
from app.core.config import settings
from app.database import get_db
from app.models.company_context import CompanyProfile, CompanyRule
from app.models.identity import User
from app.models.transaction import BankTransaction, LedgerTransaction, TransactionStatus
from app.services.reconciliation_service import ReconciliationEngine
from app.services.abuse_guard import (
    build_hardened_system_prompt,
    check_chat_rate_async,
    check_monthly_cost,
    scan_output,
)
from app.services.ai_chat_client import deploy_chat_client
from app.services.chart_of_accounts import (
    create_account,
    delete_account,
    get_chart_of_accounts,
    get_prompt_account_lines,
    update_account,
)
from app.services.rule_governance import RULE_TYPE_COMPANY_CONTEXT, RULE_TYPE_KNOWLEDGE_ARTICLE

logger = logging.getLogger(__name__)

router = APIRouter()
_engine = ReconciliationEngine()


def _user_id(user: User) -> str:
    return str(getattr(user, "id", None) or getattr(user, "user_id", None) or "system")


class LedgerImportRow(BaseModel):
    voucher_no: Optional[str] = None
    transaction_type: Optional[str] = None
    amount: Optional[str | float] = None
    currency: Optional[str] = None
    date: Optional[str] = None
    payer: Optional[str] = None
    payee: Optional[str] = None
    bank: Optional[str] = None
    memo: Optional[str] = None
    category: Optional[str] = None
    client_row_id: Optional[str] = None
    dr_cr: Optional[str] = None  # "Dr" | "Cr"


class LedgerImportRequest(BaseModel):
    import_batch_id: Optional[str] = None
    module: Optional[str] = None
    rows: List[LedgerImportRow]


class BankImportRow(BaseModel):
    date: Optional[str] = None
    amount: Optional[str | float] = None
    currency: Optional[str] = None
    account_id: Optional[str] = None
    description: Optional[str] = None
    reference: Optional[str] = None
    client_row_id: Optional[str] = None
    account_category: Optional[str] = None


class BankImportRequest(BaseModel):
    import_batch_id: Optional[str] = None
    rows: List[BankImportRow]


class TxnCategoryBulkItem(BaseModel):
    source: str # "bank" | "ledger"
    txn_id: str
    account_category: str


class TxnCategoryBulkRequest(BaseModel):
    updates: List[TxnCategoryBulkItem]
    rebuild_draft_journals: bool = False  # legacy RECON group GL only; OCR uses /ocr-journals


class LedgerDocTypeBulkItem(BaseModel):
    txn_id: str
    doc_type: str  # "AR" | "AP" (case-insensitive)


class LedgerDocTypeBulkRequest(BaseModel):
    updates: List[LedgerDocTypeBulkItem]
    rebuild_draft_journals: bool = False


class PurgeUnreconciledRequest(BaseModel):
    """Delete unreconciled recon rows that no longer exist in Books modules."""

    bank_txn_ids: List[str] = []
    ledger_txn_ids: List[str] = []


class ClearUnreconciledPoolRequest(BaseModel):
    """Wipe the entire unreconciled bank and/or ledger pool for a company."""

    bank: bool = True
    ledger: bool = True


class PurgeExceptKeptRequest(BaseModel):
    """Delete every bank/ledger row not in the keep lists (any status)."""

    keep_bank_txn_ids: List[str] = []
    keep_ledger_txn_ids: List[str] = []


class CoACreateRequest(BaseModel):
    code: str
    name_en: str
    name_zh: Optional[str] = ""
    category_type: str
    allowed_modes: List[str]
    opening_balance: Optional[float] = None
    opening_balance_dr_cr: Optional[str] = None # "Dr" | "Cr"


class CoAUpdateRequest(BaseModel):
    name_en: Optional[str] = None
    name_zh: Optional[str] = None
    category_type: Optional[str] = None
    allowed_modes: Optional[List[str]] = None
    opening_balance: Optional[float] = None
    opening_balance_dr_cr: Optional[str] = None
    clear_opening_balance: Optional[bool] = None


class CoADeleteRequest(BaseModel):
    referenced_codes: Optional[List[str]] = None


class DeployTxn(BaseModel):
    id_number: Optional[str] = None
    date: Optional[str] = None
    amount: Optional[float] = None
    payer: Optional[str] = None
    payee: Optional[str] = None
    memo: Optional[str] = None
    transaction_type: Optional[str] = None
    category: Optional[str] = None


class AccountCodeDeployRequest(BaseModel):
    transactions: List[DeployTxn]
    mode: str = "AR"
    company_profile: Optional[str] = None


@router.get("/chart-of-accounts")
async def get_chart_of_accounts_for_mode(
    mode: Optional[str] = None,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Return chart of accounts, optionally filtered by mode (AR/AP/BANK)."""
    normalized_mode = (mode or "").strip().upper() or None
    return {
        "mode": normalized_mode,
        "accounts": get_chart_of_accounts(normalized_mode, db=db, company_id=company_id),
    }


@router.post("/chart-of-accounts")
async def create_chart_of_account(
    payload: CoACreateRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Create a new Chart of Accounts entry."""
    try:
        entry = create_account(
            db=db,
            code=payload.code,
            name_en=payload.name_en,
            name_zh=payload.name_zh or "",
            category_type=payload.category_type,
            allowed_modes=payload.allowed_modes,
            opening_balance=payload.opening_balance,
            opening_balance_dr_cr=payload.opening_balance_dr_cr,
            company_id=company_id,
        )
        return {"status": "created", "account": entry}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.put("/chart-of-accounts/{code}")
async def update_chart_of_account(
    code: str,
    payload: CoAUpdateRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Update an existing Chart of Accounts entry."""
    try:
        entry = update_account(
            db=db,
            code=code,
            name_en=payload.name_en,
            name_zh=payload.name_zh,
            category_type=payload.category_type,
            allowed_modes=payload.allowed_modes,
            opening_balance=payload.opening_balance,
            opening_balance_dr_cr=payload.opening_balance_dr_cr,
            _clear_opening_balance=bool(payload.clear_opening_balance),
            company_id=company_id,
        )
        return {"status": "updated", "account": entry}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.delete("/chart-of-accounts/{code}")
async def delete_chart_of_account(
    code: str,
    payload: CoADeleteRequest = CoADeleteRequest(),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Delete a Chart of Accounts entry (blocked if default or referenced by transactions)."""
    try:
        delete_account(
            db=db,
            code=code,
            referenced_codes=payload.referenced_codes,
            company_id=company_id,
        )
        return {"status": "deleted", "code": code}
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


async def deploy_account_codes_core(
    payload: AccountCodeDeployRequest,
    company_id: str,
    db: Session,
) -> dict:
    """
    Use AI to suggest a Chart of Accounts code for each transaction.
    Returns: { results: [{id_number, suggested_code, confidence}] }
    """
    mode = (payload.mode or "AR").upper()
    account_lines = get_prompt_account_lines(mode, db=db, company_id=company_id)
    if not account_lines:
        return {
            "results": [{"id_number": t.id_number, "suggested_code": None, "confidence": 0} for t in payload.transactions]
        }

    txn_lines = []
    for i, t in enumerate(payload.transactions):
        parts = [f"{i + 1}. ID={t.id_number or '?'}"]
        if t.date:
            parts.append(f"date={t.date}")
        if t.amount is not None:
            parts.append(f"amount={t.amount}")
        if t.payer:
            parts.append(f"payer={t.payer}")
        if t.payee:
            parts.append(f"payee={t.payee}")
        if t.memo:
            parts.append(f"memo={t.memo}")
        if t.category:
            parts.append(f"category={t.category}")
        txn_lines.append(", ".join(parts))

    db_profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    db_rules = (
        db.query(CompanyRule)
        .filter(CompanyRule.company_id == company_id, CompanyRule.is_active == True)
        .order_by(CompanyRule.priority.asc())
        .all()
    )

    mode_characters = {
        "AR": (
            "You are a Hong Kong AR (Accounts Receivable) specialist. "
            "Focus on customer invoices, bank-in slips, and received cheques. "
            "The company is the PAYEE — it receives money from customers."
        ),
        "AP": (
            "You are a Hong Kong AP (Accounts Payable) specialist. "
            "Focus on invoices payable, payment slips, and issued cheques. "
            "The company is the PAYER — it pays money to vendors/suppliers."
        ),
        "BANK": (
            "You are a Hong Kong bank statement specialist. "
            "Focus on bank deposits and withdrawals. "
            "Assign codes based on transaction memo and counterparty."
        ),
    }
    character = mode_characters.get(mode, "You are a Hong Kong accounting assistant.")

    profile_block = ""
    context_row = next(
        (
            r
            for r in db_rules
            if r.rule_type == RULE_TYPE_COMPANY_CONTEXT
            and r.is_active
            and isinstance(r.rule_json, dict)
            and (r.rule_json.get("body") or "").strip()
        ),
        None,
    )
    if context_row and isinstance(context_row.rule_json, dict):
        body = (context_row.rule_json.get("body") or "").strip()
        profile_block = f"Business context:\n{body}\n\n"
    elif db_profile and db_profile.company_name:
        p_lines = [f"Company: {db_profile.company_name}"]
        if db_profile.industry:
            p_lines.append(f"Industry: {db_profile.industry}")
        if db_profile.accounting_basis:
            p_lines.append(f"Accounting basis: {db_profile.accounting_basis}")
        if db_profile.fiscal_year_end:
            p_lines.append(f"Fiscal year end: {db_profile.fiscal_year_end}")
        profile_block = "Company profile:\n" + "\n".join(p_lines) + "\n\n"

    rules_block = ""
    rule_lines: list[str] = []
    for r in db_rules:
        if r.rule_type in ("document_gate", RULE_TYPE_COMPANY_CONTEXT):
            continue
        if r.rule_type == RULE_TYPE_KNOWLEDGE_ARTICLE:
            rj = r.rule_json if isinstance(r.rule_json, dict) else {}
            uw = (rj.get("use_when") or "").strip()
            body = (rj.get("body") or "").strip()
            parts = [f"- {r.rule_name}"]
            if uw:
                parts.append(f"when={uw}")
            if body:
                parts.append(f"guidance={body}")
            rule_lines.append(", ".join(parts))
            continue
        parts = [f"- {r.rule_name}"]
        if r.keyword_pattern:
            parts.append(f"keyword={r.keyword_pattern}")
        if r.vendor_pattern:
            parts.append(f"vendor={r.vendor_pattern}")
        if r.amount_pattern:
            parts.append(f"amount={r.amount_pattern}")
        if r.notes:
            parts.append(f"notes={r.notes}")
        rule_lines.append(", ".join(parts))
    if rule_lines:
        rules_block = (
            "Company classification rules (apply these first, they override generic logic):\n"
            + "\n".join(rule_lines)
            + "\n\n"
        )

    if mode == "BANK":
        txn_fields_note = "Transaction fields provided: ID, date, amount, payer, payee, memo (BANK mode)\n\n"
    else:
        txn_fields_note = "Transaction fields provided: ID, date, amount, payer, payee, memo, category\n\n"

    accounts_block = f"Available Chart of Accounts (mode={mode}):\n" + "\n".join(account_lines) + "\n\n"

    output_rules = (
        "Rules:\n"
        "- Reply ONLY with valid JSON: an array of objects with keys: id_number, suggested_code, confidence (0.0-1.0)\n"
        "- suggested_code must be one of the codes listed above, or null if no match\n"
        "- Do not add any commentary outside the JSON array\n"
    )

    system_prompt = build_hardened_system_prompt(
        character + "\n\n" + profile_block + rules_block + txn_fields_note + accounts_block + output_rules
    )

    user_msg = "Transactions to classify:\n" + "\n".join(txn_lines)

    try:
        deploy_model = settings.deploy_model
        if not deploy_chat_client.api_key or not deploy_chat_client.base_url or not deploy_model:
            raise HTTPException(
                status_code=503,
                detail="LLM API not configured. Set LLM_API_KEY, LLM_BASE_URL, LLM_MODEL in .env",
            )
        raw_text, _raw_response = await asyncio.to_thread(
            deploy_chat_client.complete,
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            deploy_model,
        )
        _safe, raw_text = scan_output(raw_text, company_id)
        if not _safe:
            return {"results": []}
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            raw_text = raw_text.strip()

        results = json.loads(raw_text)
        if not isinstance(results, list):
            results = []
        return {"results": results}
    except json.JSONDecodeError as e:
        logger.warning("[deploy] JSON parse error: %s", e)
        return {
            "results": [{"id_number": t.id_number, "suggested_code": None, "confidence": 0} for t in payload.transactions]
        }
    except Exception as e:
        logger.error("[deploy] Error: %s", e)
        raise HTTPException(status_code=500, detail=f"Account code deploy failed: {str(e)}") from e


@router.post("/account-codes/deploy")
async def deploy_account_codes(
    payload: AccountCodeDeployRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    rate_ok, rate_msg = await check_chat_rate_async(company_id)
    if not rate_ok:
        raise HTTPException(status_code=429, detail=rate_msg)
    cost_ok, cost_msg = check_monthly_cost(db, company_id)
    if not cost_ok:
        raise HTTPException(status_code=429, detail=cost_msg)
    return await deploy_account_codes_core(payload, company_id, db)


def _parse_amount(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_ledger_amount_dr_cr(
    amount: float,
    *,
    module: Optional[str],
    transaction_type: Optional[str],
    explicit_dr_cr: Optional[str],
) -> tuple[float, str]:
    """Magnitude + Dr/Cr. Explicit side wins; else AP→Dr / AR→Cr; negative amount flips default."""
    mag = abs(float(amount))
    raw = (explicit_dr_cr or "").strip().capitalize()
    if raw in ("Dr", "Cr"):
        return mag, raw
    mod = (module or transaction_type or "").strip().upper()
    side = "Dr" if mod == "AP" else "Cr"
    if float(amount) < 0:
        side = "Cr" if side == "Dr" else "Dr"
    return mag, side


def _parse_date(value: object) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


@router.post("/ledger-import")
async def import_ledger_transactions(
    payload: LedgerImportRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Import ledger transactions from AR/AP spreadsheet rows.

    Unreconciled rows that already exist are updated from Books (dr_cr, etc.)
    so module edits like AP Credit vs Debit sync into Reconciliation.
    """
    batch_id = payload.import_batch_id or str(uuid.uuid4())
    stored_count = 0
    updated_count = 0
    created_rows: List[dict] = []

    for row in payload.rows:
        amount = _parse_amount(row.amount)
        book_date = _parse_date(row.date)
        if amount is None or book_date is None:
            continue

        amount_mag, dr_cr = _normalize_ledger_amount_dr_cr(
            amount,
            module=payload.module,
            transaction_type=row.transaction_type,
            explicit_dr_cr=row.dr_cr,
        )
        counterparty = row.payee or row.payer or ""
        doc_id = (row.voucher_no or "").strip() or None
        module = (payload.module or "").strip() or None
        reference = row.voucher_no or row.memo
        existing = (
            db.query(LedgerTransaction)
            .filter(
                LedgerTransaction.company_id == company_id,
                LedgerTransaction.module == module,
                LedgerTransaction.doc_id == doc_id,
                LedgerTransaction.book_date == book_date,
                LedgerTransaction.amount == amount_mag,
                LedgerTransaction.status == TransactionStatus.UNRECONCILED,
            )
            .first()
        )
        if existing:
            # Books is source of truth for side / display fields on open rows.
            existing.dr_cr = dr_cr
            existing.doc_type = row.transaction_type or existing.doc_type or "receipt"
            existing.currency = row.currency or existing.currency or "HKD"
            existing.counterparty = counterparty
            if row.category is not None:
                existing.account_category = row.category
            if reference:
                existing.reference = reference
            updated_count += 1
            created_rows.append(
                {
                    "id": existing.id,
                    "client_row_id": row.client_row_id,
                    "voucher_no": existing.doc_id,
                    "transaction_type": existing.doc_type,
                    "amount": float(existing.amount),
                    "dr_cr": existing.dr_cr,
                    "currency": existing.currency,
                    "date": row.date,
                    "memo": row.memo,
                    "category": existing.account_category,
                    "import_batch_id": existing.import_batch_id,
                    "updated": True,
                }
            )
            continue

        ledger_txn = LedgerTransaction(
            id=str(uuid.uuid4()),
            company_id=company_id,
            module=module,
            doc_type=row.transaction_type or "receipt",
            doc_id=doc_id,
            book_date=book_date,
            amount=amount_mag,
            currency=row.currency or "HKD",
            counterparty=counterparty,
            account_category=row.category,
            # Prefer voucher/id for Reference; memo is often OCR noise.
            reference=reference,
            import_batch_id=batch_id,
            dr_cr=dr_cr,
            status=TransactionStatus.UNRECONCILED,
        )
        db.add(ledger_txn)
        stored_count += 1
        created_rows.append(
            {
                "id": ledger_txn.id,
                "client_row_id": row.client_row_id,
                "voucher_no": row.voucher_no,
                "transaction_type": row.transaction_type,
                "amount": amount_mag,
                "dr_cr": dr_cr,
                "currency": row.currency or "HKD",
                "date": row.date,
                "memo": row.memo,
                "category": row.category,
                "import_batch_id": batch_id,
            }
        )

    if stored_count or updated_count:
        db.commit()

    for row in created_rows:
        tid = (row.get("id") or "").strip()
        if not tid:
            continue
        try:
            from app.services import gl_journal_service as glsvc

            glsvc.ensure_draft_for_txn(db, company_id, ledger_txn_id=tid)
        except Exception:
            logger.exception("GL ensure_draft_for_txn failed for ledger %s", tid)

    return {
        "import_batch_id": batch_id,
        "stored_count": stored_count,
        "updated_count": updated_count,
        "created_rows": created_rows,
    }


@router.post("/bank-import")
async def import_bank_transactions(
    payload: BankImportRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Import bank transactions from Books bank spreadsheet rows."""
    batch_id = payload.import_batch_id or str(uuid.uuid4())
    stored_count = 0
    created_rows: List[dict] = []

    for row in payload.rows:
        amount = _parse_amount(row.amount)
        bank_date = _parse_date(row.date)
        if amount is None or bank_date is None:
            continue

        account_id = (row.account_id or "UNKNOWN").strip() or "UNKNOWN"
        existing = (
            db.query(BankTransaction)
            .filter(
                BankTransaction.company_id == company_id,
                BankTransaction.bank_date == bank_date,
                BankTransaction.amount == amount,
                BankTransaction.account_id == account_id,
                BankTransaction.status == TransactionStatus.UNRECONCILED,
            )
            .first()
        )
        if existing:
            created_rows.append(
                {
                    "id": existing.id,
                    "client_row_id": row.client_row_id,
                    "reference": existing.reference,
                    "amount": float(existing.amount),
                    "currency": existing.currency,
                    "date": row.date,
                    "import_batch_id": existing.import_batch_id,
                }
            )
            continue

        description = row.description or row.reference or ""
        bank_txn = BankTransaction(
            id=str(uuid.uuid4()),
            company_id=company_id,
            account_id=account_id,
            bank_date=bank_date,
            amount=amount,
            currency=row.currency or "HKD",
            description_raw=str(description),
            description_norm=str(description).lower(),
            account_category=row.account_category,
            reference=row.reference,
            import_batch_id=batch_id,
            status=TransactionStatus.UNRECONCILED,
        )
        db.add(bank_txn)
        stored_count += 1
        created_rows.append(
            {
                "id": bank_txn.id,
                "client_row_id": row.client_row_id,
                "reference": bank_txn.reference,
                "amount": amount,
                "currency": row.currency or "HKD",
                "date": row.date,
                "import_batch_id": batch_id,
            }
        )

    if stored_count:
        db.commit()

    for row in created_rows:
        tid = (row.get("id") or "").strip()
        if not tid:
            continue
        try:
            from app.services import gl_journal_service as glsvc

            glsvc.ensure_draft_for_txn(db, company_id, bank_txn_id=tid)
        except Exception:
            logger.exception("GL ensure_draft_for_txn failed for bank %s", tid)

    return {"import_batch_id": batch_id, "stored_count": stored_count, "created_rows": created_rows}


@router.get("/bank-transactions")
async def get_bank_transactions(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Get all bank transactions for the company."""
    try:
        transactions = db.query(BankTransaction).filter(BankTransaction.company_id == company_id).all()
        return [
            {
                "id": t.id,
                "company_id": t.company_id,
                "account_id": t.account_id,
                "bank_date": t.bank_date.isoformat() if t.bank_date else None,
                "amount": float(t.amount),
                "currency": t.currency,
                "description_raw": t.description_raw,
                "description_norm": t.description_norm,
                "account_category": t.account_category,
                "reference": t.reference,
                "import_batch_id": t.import_batch_id,
                "status": t.status.value if hasattr(t.status, "value") else t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in transactions
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch bank transactions: {str(e)}") from e


@router.get("/ledger-transactions")
async def get_ledger_transactions(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Get all ledger transactions for the company."""
    try:
        transactions = db.query(LedgerTransaction).filter(LedgerTransaction.company_id == company_id).all()
        return [
            {
                "id": t.id,
                "company_id": t.company_id,
                "module": t.module,
                "doc_type": t.doc_type,
                "doc_id": t.doc_id,
                "book_date": t.book_date.isoformat() if t.book_date else None,
                "amount": float(t.amount),
                "currency": t.currency,
                "counterparty": t.counterparty,
                "account_category": t.account_category,
                "reference": t.reference,
                "import_batch_id": t.import_batch_id,
                "dr_cr": t.dr_cr,
                "status": t.status.value if hasattr(t.status, "value") else t.status,
                "created_at": t.created_at.isoformat() if t.created_at else None,
                "updated_at": t.updated_at.isoformat() if t.updated_at else None,
            }
            for t in transactions
        ]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to fetch ledger transactions: {str(e)}") from e


def _purge_txn_ids(
    db: Session,
    company_id: str,
    bank_ids: List[str],
    ledger_ids: List[str],
    *,
    only_unreconciled: bool = True,
) -> tuple[int, int]:
    """Permanently delete bank/ledger rows by id (and draft journals / match links)."""
    from app.models.gl_journal import GlJournalStatus
    from app.models.reconciliation import ReconciliationMatch
    from app.services import gl_journal_service as glsvc
    from app.services.gl_journal_service import _delete_journal_cascade, _module_journals_for_txn_ids

    bank_ids = [i for i in dict.fromkeys(bank_ids or []) if i]
    ledger_ids = [i for i in dict.fromkeys(ledger_ids or []) if i]
    if not bank_ids and not ledger_ids:
        return 0, 0

    bank_set = set(bank_ids)
    ledger_set = set(ledger_ids)

    for j in _module_journals_for_txn_ids(db, company_id, bank_set, ledger_set):
        if j.status == GlJournalStatus.DRAFT:
            _delete_journal_cascade(db, j)

    if bank_ids:
        db.query(ReconciliationMatch).filter(
            ReconciliationMatch.company_id == company_id,
            ReconciliationMatch.bank_txn_id.in_(bank_ids),
        ).delete(synchronize_session=False)
    if ledger_ids:
        db.query(ReconciliationMatch).filter(
            ReconciliationMatch.company_id == company_id,
            ReconciliationMatch.ledger_txn_id.in_(ledger_ids),
        ).delete(synchronize_session=False)

    purged_bank = 0
    purged_ledger = 0
    if bank_ids:
        q = db.query(BankTransaction).filter(
            BankTransaction.company_id == company_id,
            BankTransaction.id.in_(bank_ids),
        )
        if only_unreconciled:
            q = q.filter(BankTransaction.status == TransactionStatus.UNRECONCILED)
        purged_bank = q.delete(synchronize_session=False)
    if ledger_ids:
        q = db.query(LedgerTransaction).filter(
            LedgerTransaction.company_id == company_id,
            LedgerTransaction.id.in_(ledger_ids),
        )
        if only_unreconciled:
            q = q.filter(LedgerTransaction.status == TransactionStatus.UNRECONCILED)
        purged_ledger = q.delete(synchronize_session=False)

    if purged_bank or purged_ledger:
        db.commit()
        glsvc.prune_orphan_recon_draft_journals(db, company_id)
    return int(purged_bank or 0), int(purged_ledger or 0)


def _purge_unreconciled_ids(
    db: Session,
    company_id: str,
    bank_ids: List[str],
    ledger_ids: List[str],
) -> tuple[int, int]:
    return _purge_txn_ids(db, company_id, bank_ids, ledger_ids, only_unreconciled=True)


@router.post("/purge-unreconciled")
async def purge_unreconciled_transactions(
    payload: PurgeUnreconciledRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Remove specific unreconciled bank/ledger rows. Matched/partial rows are left untouched."""
    purged_bank, purged_ledger = _purge_unreconciled_ids(
        db, company_id, payload.bank_txn_ids or [], payload.ledger_txn_ids or []
    )
    return {"purged_bank": purged_bank, "purged_ledger": purged_ledger}


@router.post("/clear-unreconciled-pool")
async def clear_unreconciled_pool(
    payload: ClearUnreconciledPoolRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Permanently wipe all unreconciled bank/ledger rows so the pool can be rebuilt from Books.

    Matched/partial rows are kept for match history. Callers should re-import current module rows after.
    """
    bank_ids: List[str] = []
    ledger_ids: List[str] = []
    if payload.bank:
        bank_ids = [
            r[0]
            for r in db.query(BankTransaction.id)
            .filter(
                BankTransaction.company_id == company_id,
                BankTransaction.status == TransactionStatus.UNRECONCILED,
            )
            .all()
        ]
    if payload.ledger:
        ledger_ids = [
            r[0]
            for r in db.query(LedgerTransaction.id)
            .filter(
                LedgerTransaction.company_id == company_id,
                LedgerTransaction.status == TransactionStatus.UNRECONCILED,
            )
            .all()
        ]
    purged_bank, purged_ledger = _purge_unreconciled_ids(db, company_id, bank_ids, ledger_ids)
    return {"purged_bank": purged_bank, "purged_ledger": purged_ledger}


@router.post("/purge-except-kept")
async def purge_except_kept(
    payload: PurgeExceptKeptRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Permanently delete every bank/ledger row not listed in keep_* (any status).

    Used by Books→recon sync so the pool matches modules: keep matched rows that still
    exist in modules; delete unreconciled extras and matched orphans.
    """
    keep_bank = {i for i in (payload.keep_bank_txn_ids or []) if i}
    keep_ledger = {i for i in (payload.keep_ledger_txn_ids or []) if i}
    bank_ids = [
        r[0]
        for r in db.query(BankTransaction.id).filter(BankTransaction.company_id == company_id).all()
        if r[0] not in keep_bank
    ]
    ledger_ids = [
        r[0]
        for r in db.query(LedgerTransaction.id)
        .filter(LedgerTransaction.company_id == company_id)
        .all()
        if r[0] not in keep_ledger
    ]
    purged_bank, purged_ledger = _purge_txn_ids(
        db, company_id, bank_ids, ledger_ids, only_unreconciled=False
    )
    return {"purged_bank": purged_bank, "purged_ledger": purged_ledger}


class DissolveGroupRequest(BaseModel):
    group_id: str
    reason: str = ""


@router.post("/dissolve-group")
async def dissolve_group(
    payload: DissolveGroupRequest,
    company_id: str = Depends(get_current_company_id),
    trace_id: str = Depends(get_trace_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel/dissolve a recon match group (including orphan 0-member groups)."""
    try:
        return await _engine.dissolve_group(
            payload.group_id,
            company_id,
            _user_id(user),
            trace_id,
            payload.reason or "user_cancel_match",
            db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/transactions/account-category-bulk")
async def bulk_transaction_account_category(
    payload: TxnCategoryBulkRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Persist OCR / deploy codes to bank_transactions / ledger_transactions.account_category."""
    from app.services import gl_journal_service as glsvc

    tuples: list[tuple[str, str, str]] = []
    for u in payload.updates:
        src = (u.source or "").strip().lower()
        if src not in ("bank", "ledger"):
            raise HTTPException(status_code=422, detail="Each update.source must be 'bank' or 'ledger'")
        tid = (u.txn_id or "").strip()
        if not tid:
            continue
        tuples.append((src, tid, u.account_category or ""))

    if not tuples:
        return {"updated_count": 0, "rebuilt_group_ids": []}

    try:
        glsvc.assert_account_category_updates_not_blocked_by_posted_gl(db, company_id, tuples)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    n, bank_ids, ledger_ids = glsvc.bulk_set_transaction_account_categories(db, company_id, tuples)
    rebuilt: list[str] = []
    if payload.rebuild_draft_journals and (bank_ids or ledger_ids):
        gids = glsvc.draft_group_ids_with_primary_draft(db, company_id, bank_ids, ledger_ids)
        for gid in gids:
            try:
                glsvc.rebuild_primary_draft_for_group(db, company_id, gid)
                rebuilt.append(gid)
            except ValueError:
                continue
    return {"updated_count": n, "rebuilt_group_ids": rebuilt}


@router.post("/transactions/ledger-doc-type-bulk")
async def bulk_ledger_doc_type(
    payload: LedgerDocTypeBulkRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Persist OCR AR/AP type to ledger_transactions.doc_type."""
    from app.services import gl_journal_service as glsvc

    doc_updates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for u in payload.updates:
        tid = (u.txn_id or "").strip()
        if not tid or tid in seen:
            continue
        dt = (u.doc_type or "").strip().upper()
        if dt not in ("AR", "AP"):
            raise HTTPException(status_code=422, detail="Each update.doc_type must be AR or AP")
        seen.add(tid)
        doc_updates.append((tid, dt))

    if not doc_updates:
        return {"updated_count": 0, "rebuilt_group_ids": []}

    block_tuples = [("ledger", tid, "") for tid, _ in doc_updates]
    try:
        glsvc.assert_account_category_updates_not_blocked_by_posted_gl(
            db,
            company_id,
            block_tuples,
            blocked_action="change AR/AP type",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    n, ledger_ids = glsvc.bulk_set_ledger_doc_types(db, company_id, doc_updates)
    rebuilt: list[str] = []
    if payload.rebuild_draft_journals and ledger_ids:
        gids = glsvc.draft_group_ids_with_primary_draft(db, company_id, set(), ledger_ids)
        for gid in gids:
            try:
                glsvc.rebuild_primary_draft_for_group(db, company_id, gid)
                rebuilt.append(gid)
            except ValueError:
                continue
    return {"updated_count": n, "rebuilt_group_ids": rebuilt}


from app.api.reconciliation_match_gl import router as match_gl_router

router.include_router(match_gl_router)

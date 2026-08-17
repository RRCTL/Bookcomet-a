"""Match, session, AI-match, and GL journal HTTP routes for bank reconciliation."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user, get_trace_id
from app.database import get_db
from app.models.gl_journal import GlJournal
from app.models.identity import User
from app.models.reconciliation import ReconSession, ReconciliationGroup, ReconciliationMatch
from app.models.transaction import BankTransaction, LedgerTransaction, TransactionStatus
from app.services import gl_journal_service as glsvc
from app.services.ai_recon_service import run_ai_match
from app.services.reconciliation_service import ReconciliationEngine

router = APIRouter()
_engine = ReconciliationEngine()
logger = logging.getLogger(__name__)


# ── Serializers ─────────────────────────────────────────────────────────────


def _status_val(s) -> str:
    return s.value if hasattr(s, "value") else str(s)


def _bank_txn_dict(t: BankTransaction) -> dict[str, Any]:
    return {
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
        "status": _status_val(t.status),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _ledger_txn_dict(t: LedgerTransaction) -> dict[str, Any]:
    return {
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
        "status": _status_val(t.status),
        "created_at": t.created_at.isoformat() if t.created_at else None,
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _group_dict(db: Session, group: ReconciliationGroup) -> dict[str, Any]:
    rows = (
        db.query(ReconciliationMatch)
        .filter(
            ReconciliationMatch.group_id == group.id,
            ReconciliationMatch.company_id == group.company_id,
        )
        .all()
    )
    bank_ids = [r.bank_txn_id for r in rows if r.bank_txn_id]
    ledger_ids = [r.ledger_txn_id for r in rows if r.ledger_txn_id]
    bank_txns = (
        db.query(BankTransaction).filter(BankTransaction.id.in_(bank_ids)).all()
        if bank_ids
        else []
    )
    ledger_txns = (
        db.query(LedgerTransaction).filter(LedgerTransaction.id.in_(ledger_ids)).all()
        if ledger_ids
        else []
    )
    # Same-mode when group has members on only one physical table type per side convention
    card = (group.match_cardinality or "").strip()
    is_same_mode = card in ("N:N", "N:1", "1:N") and (
        (not bank_ids and not ledger_ids)
        or (len(bank_txns) == 0 and len(ledger_txns) > 0 and card.startswith("0:") is False)
    )
    if card == "N:0" or card.startswith("0:"):
        is_same_mode = False
    return {
        "id": group.id,
        "match_cardinality": group.match_cardinality,
        "total_bank_amount": float(group.total_bank_amount),
        "total_ledger_amount": float(group.total_ledger_amount),
        "difference": float(group.difference),
        "partial_remainder_txn_id": group.partial_remainder_txn_id,
        "created_by": group.created_by,
        "created_at": group.created_at.isoformat() if group.created_at else None,
        "bank_txn_ids": bank_ids,
        "ledger_txn_ids": ledger_ids,
        "bank_txns": [_bank_txn_dict(b) for b in bank_txns],
        "ledger_txns": [_ledger_txn_dict(l) for l in ledger_txns],
        "is_same_mode": is_same_mode,
    }


# ── Request models ──────────────────────────────────────────────────────────


class AutoMatchSelectedRequest(BaseModel):
    bank_txn_ids: List[str] = Field(default_factory=list)
    ledger_txn_ids: List[str] = Field(default_factory=list)


class MultiManualMatchRequest(BaseModel):
    bank_txn_ids: List[str] = Field(default_factory=list)
    ledger_txn_ids: List[str] = Field(default_factory=list)


class ClearBankRequest(BaseModel):
    bank_txn_ids: List[str] = Field(default_factory=list)


class GlOnlyMatchRequest(BaseModel):
    bank_txn_ids: List[str] = Field(default_factory=list)


class LedgerPendingMatchRequest(BaseModel):
    ledger_txn_ids: List[str] = Field(default_factory=list)


class GlPostRequest(BaseModel):
    confirm_bank_create: bool = False


class GroupUnmatchMemberRequest(BaseModel):
    group_id: str
    txn_id: str
    txn_type: str
    reason: str = ""


class AiMatchRequest(BaseModel):
    bank_txn_ids: List[str] = Field(default_factory=list)
    ledger_txn_ids: List[str] = Field(default_factory=list)
    task_id: Optional[str] = None


class SessionEntry(BaseModel):
    txn_id: str
    txn_type: str
    raw_txn_data: Optional[dict] = None
    display_row: Optional[dict] = None


class SessionSaveRequest(BaseModel):
    entries: Optional[List[SessionEntry]] = None
    workspace: Optional[dict] = None


RECON_WORKSPACE_TXN_ID = "__workspace__"


class GlPatchLine(BaseModel):
    id: Optional[str] = None
    account_code: Optional[str] = None
    debit: Optional[float] = None
    credit: Optional[float] = None
    memo: Optional[str] = None


class GlPatchRequest(BaseModel):
    journal_date: Optional[str] = None
    balancing_account_code: Optional[str] = None
    deleted_line_ids: Optional[List[str]] = None
    lines: Optional[List[GlPatchLine]] = None


class GlUnpostRequest(BaseModel):
    journal_id: str


class GlManualLineIn(BaseModel):
    account_code: str
    debit: float = 0.0
    credit: float = 0.0
    memo: Optional[str] = None


class GlManualCreateRequest(BaseModel):
    journal_date: str
    currency: str = "HKD"
    narration: Optional[str] = None
    voucher_no: Optional[str] = None
    lines: List[GlManualLineIn] = Field(default_factory=list)


class GlEnsureDraftTxnRequest(BaseModel):
    bank_txn_id: Optional[str] = None
    ledger_txn_id: Optional[str] = None


def _user_id(user: User) -> str:
    return user.id


def _parse_journal_date(raw: str) -> datetime:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.strptime(raw[:10], "%Y-%m-%d")


# ── Match routes ────────────────────────────────────────────────────────────


@router.post("/auto-match-selected")
async def auto_match_selected(
    payload: AutoMatchSelectedRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    bank_ids = [i for i in dict.fromkeys(payload.bank_txn_ids or []) if i]
    ledger_ids = [i for i in dict.fromkeys(payload.ledger_txn_ids or []) if i]
    if not bank_ids or not ledger_ids:
        return {"total_matches": 0, "auto_matches": 0, "manual_review": 0, "matches": []}

    bank_txns = db.query(BankTransaction).filter(
        BankTransaction.id.in_(bank_ids),
        BankTransaction.company_id == company_id,
    ).all()
    ledger_txns = db.query(LedgerTransaction).filter(
        LedgerTransaction.id.in_(ledger_ids),
        LedgerTransaction.company_id == company_id,
    ).all()

    raw = await _engine.auto_match(bank_txns, ledger_txns)
    matches_out = []
    auto_n = 0
    manual_n = 0
    for m in raw:
        bank = m["bank_txn"]
        ledger = m["ledger_txn"]
        decision = m["decision"]
        dec_str = decision.value if hasattr(decision, "value") else str(decision)
        if dec_str == "auto":
            auto_n += 1
        else:
            manual_n += 1
        mt = m["match_type"]
        mt_str = mt.value if hasattr(mt, "value") else str(mt)
        matches_out.append(
            {
                "bank_txn_id": bank.id,
                "ledger_txn_id": ledger.id,
                "score": float(m["score"]),
                "match_type": mt_str,
                "decision": dec_str,
            }
        )
    return {
        "total_matches": len(matches_out),
        "auto_matches": auto_n,
        "manual_review": manual_n,
        "matches": matches_out,
    }


@router.post("/multi-manual-match")
async def multi_manual_match(
    payload: MultiManualMatchRequest,
    company_id: str = Depends(get_current_company_id),
    trace_id: str = Depends(get_trace_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return await _engine.multi_manual_match(
            payload.bank_txn_ids,
            payload.ledger_txn_ids,
            company_id,
            _user_id(user),
            trace_id,
            db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/clear-bank-transactions")
async def clear_bank_transactions(
    payload: ClearBankRequest,
    company_id: str = Depends(get_current_company_id),
    trace_id: str = Depends(get_trace_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return await _engine.clear_bank_transactions(
            payload.bank_txn_ids,
            company_id,
            _user_id(user),
            trace_id,
            db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gl-only-match")
async def gl_only_match(
    payload: GlOnlyMatchRequest,
    company_id: str = Depends(get_current_company_id),
    trace_id: str = Depends(get_trace_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return await _engine.gl_only_match(
            payload.bank_txn_ids,
            company_id,
            _user_id(user),
            trace_id,
            db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ledger-pending-match")
async def ledger_pending_match(
    payload: LedgerPendingMatchRequest,
    company_id: str = Depends(get_current_company_id),
    trace_id: str = Depends(get_trace_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        return await _engine.ledger_pending_bank_match(
            payload.ledger_txn_ids,
            company_id,
            _user_id(user),
            trace_id,
            db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/group-unmatch-member")
async def group_unmatch_member(
    payload: GroupUnmatchMemberRequest,
    company_id: str = Depends(get_current_company_id),
    trace_id: str = Depends(get_trace_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    txn_type = (payload.txn_type or "").strip().lower()
    if txn_type not in ("bank", "ledger"):
        raise HTTPException(status_code=422, detail="txn_type must be 'bank' or 'ledger'")
    try:
        return await _engine.remove_group_member(
            payload.group_id,
            payload.txn_id,
            txn_type,
            company_id,
            _user_id(user),
            trace_id,
            payload.reason or "user_unmatch",
            db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai-match")
async def ai_match(
    payload: AiMatchRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    bank_ids = [i for i in dict.fromkeys(payload.bank_txn_ids or []) if i]
    ledger_ids = [i for i in dict.fromkeys(payload.ledger_txn_ids or []) if i]
    bank_txns = db.query(BankTransaction).filter(
        BankTransaction.id.in_(bank_ids),
        BankTransaction.company_id == company_id,
    ).all()
    ledger_txns = db.query(LedgerTransaction).filter(
        LedgerTransaction.id.in_(ledger_ids),
        LedgerTransaction.company_id == company_id,
    ).all()
    if not bank_txns or not ledger_txns:
        raise HTTPException(
            status_code=404,
            detail="Selected transactions were not found. Refresh and try again.",
        )
    logger.info(
        "[ai-match] start company=%s bank=%s ledger=%s",
        company_id,
        len(bank_txns),
        len(ledger_txns),
    )
    try:
        result, _raw = await asyncio.to_thread(
            run_ai_match,
            [_bank_txn_dict(b) for b in bank_txns],
            [_ledger_txn_dict(l) for l in ledger_txns],
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("ai_match failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info(
        "[ai-match] done company=%s matches=%s",
        company_id,
        len(result.get("matches") or []),
    )
    return result


@router.get("/groups")
async def list_groups(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    groups = (
        db.query(ReconciliationGroup)
        .filter(ReconciliationGroup.company_id == company_id)
        .order_by(ReconciliationGroup.created_at.desc())
        .all()
    )
    return {"groups": [_group_dict(db, g) for g in groups]}


@router.get("/partial-transactions")
async def partial_transactions(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    txns = (
        db.query(BankTransaction)
        .filter(
            BankTransaction.company_id == company_id,
            BankTransaction.status == TransactionStatus.PARTIAL,
        )
        .all()
    )
    partial = []
    for t in txns:
        match_row = (
            db.query(ReconciliationMatch)
            .filter(ReconciliationMatch.bank_txn_id == t.id)
            .first()
        )
        group = None
        if match_row and match_row.group_id:
            group = db.query(ReconciliationGroup).filter(
                ReconciliationGroup.id == match_row.group_id
            ).first()
        partial.append(
            {
                "id": t.id,
                "bank_date": t.bank_date.isoformat() if t.bank_date else None,
                "amount": float(t.amount),
                "currency": t.currency,
                "description_raw": t.description_raw,
                "reference": t.reference,
                "group": {
                    "id": group.id,
                    "match_cardinality": group.match_cardinality,
                    "total_bank_amount": float(group.total_bank_amount),
                    "total_ledger_amount": float(group.total_ledger_amount),
                    "difference": float(group.difference),
                    "bank_member_ids": [],
                    "ledger_member_ids": [],
                }
                if group
                else None,
            }
        )
    return {"partial_transactions": partial, "count": len(partial)}


# ── Session routes ──────────────────────────────────────────────────────────


@router.get("/session")
async def get_session(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ReconSession)
        .filter(ReconSession.company_id == company_id)
        .all()
    )
    bank_txns: list[dict] = []
    ledger_txns: list[dict] = []
    bank_rows: list[dict] = []
    ledger_rows: list[dict] = []
    workspace: dict | None = None
    for r in rows:
        if r.txn_id == RECON_WORKSPACE_TXN_ID and r.txn_type == "meta":
            if isinstance(r.raw_txn_data, dict):
                workspace = r.raw_txn_data
            continue
        raw = r.raw_txn_data if isinstance(r.raw_txn_data, dict) else {}
        disp = r.display_row if isinstance(r.display_row, dict) else None
        if r.txn_type == "bank":
            bank_txns.append(raw)
            if disp:
                bank_rows.append(disp)
        elif r.txn_type == "ledger":
            ledger_txns.append(raw)
            if disp:
                ledger_rows.append(disp)
    return {
        "bank_txns": bank_txns,
        "ledger_txns": ledger_txns,
        "bank_rows": bank_rows,
        "ledger_rows": ledger_rows,
        "workspace": workspace,
    }


@router.put("/session")
async def save_session(
    payload: SessionSaveRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    if payload.entries is None and payload.workspace is None:
        raise HTTPException(status_code=400, detail="Nothing to save")

    count = 0
    if payload.entries is not None:
        db.query(ReconSession).filter(
            ReconSession.company_id == company_id,
            ReconSession.txn_type.in_(("bank", "ledger")),
        ).delete(synchronize_session=False)
        for entry in payload.entries:
            tid = (entry.txn_id or "").strip()
            ttype = (entry.txn_type or "").strip().lower()
            if not tid or ttype not in ("bank", "ledger"):
                continue
            db.add(
                ReconSession(
                    id=str(uuid.uuid4()),
                    company_id=company_id,
                    txn_id=tid,
                    txn_type=ttype,
                    raw_txn_data=entry.raw_txn_data,
                    display_row=entry.display_row,
                )
            )
            count += 1
    if payload.workspace is not None:
        meta = (
            db.query(ReconSession)
            .filter(
                ReconSession.company_id == company_id,
                ReconSession.txn_id == RECON_WORKSPACE_TXN_ID,
            )
            .first()
        )
        if meta:
            meta.raw_txn_data = payload.workspace
            meta.txn_type = "meta"
        else:
            db.add(
                ReconSession(
                    id=str(uuid.uuid4()),
                    company_id=company_id,
                    txn_id=RECON_WORKSPACE_TXN_ID,
                    txn_type="meta",
                    raw_txn_data=payload.workspace,
                )
            )
    db.commit()
    return {"status": "ok", "count": count}


@router.delete("/reset")
async def reset_recon(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    deleted_groups = (
        db.query(ReconciliationGroup)
        .filter(ReconciliationGroup.company_id == company_id)
        .delete(synchronize_session=False)
    )
    deleted_matches = (
        db.query(ReconciliationMatch)
        .filter(ReconciliationMatch.company_id == company_id)
        .delete(synchronize_session=False)
    )
    deleted_session = (
        db.query(ReconSession)
        .filter(ReconSession.company_id == company_id)
        .delete(synchronize_session=False)
    )
    db.query(BankTransaction).filter(BankTransaction.company_id == company_id).update(
        {BankTransaction.status: TransactionStatus.UNRECONCILED},
        synchronize_session=False,
    )
    db.query(LedgerTransaction).filter(
        LedgerTransaction.company_id == company_id
    ).update(
        {LedgerTransaction.status: TransactionStatus.UNRECONCILED},
        synchronize_session=False,
    )
    glsvc.prune_orphan_recon_draft_journals(db, company_id)
    db.commit()
    return {
        "status": "ok",
        "deleted_groups": deleted_groups,
        "deleted_matches": deleted_matches,
        "deleted_session": deleted_session,
    }


# ── GL journal routes ───────────────────────────────────────────────────────


@router.get("/gl")
async def gl_list(
    status: Optional[str] = Query(None),
    currency: Optional[str] = Query(None),
    source: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    df = _parse_journal_date(date_from) if date_from else None
    dt = _parse_journal_date(date_to) if date_to else None
    try:
        journals = glsvc.list_journals(
            db,
            company_id,
            status=status,
            currency=currency,
            source=source,
            date_from=df,
            date_to=dt,
            limit=limit,
        )
        return {"journals": journals}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gl/manual")
async def gl_create_manual(
    payload: GlManualCreateRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        journal = glsvc.create_manual_journal(
            db,
            company_id,
            journal_date=_parse_journal_date(payload.journal_date),
            currency=payload.currency,
            narration=payload.narration,
            voucher_no=payload.voucher_no,
            lines=[ln.model_dump() for ln in payload.lines],
        )
        return glsvc.journal_to_dict(db, journal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gl/ensure-draft-txn")
async def gl_ensure_draft_txn(
    payload: GlEnsureDraftTxnRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        journal = glsvc.ensure_draft_for_txn(
            db,
            company_id,
            bank_txn_id=payload.bank_txn_id,
            ledger_txn_id=payload.ledger_txn_id,
        )
        return glsvc.journal_to_dict(db, journal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gl/ensure-draft")
async def gl_ensure_draft(
    group_id: str = Query(...),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        journal = glsvc.ensure_draft_for_group(db, company_id, group_id)
        return glsvc.journal_to_dict(db, journal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/gl/by-group/{group_id}")
async def gl_by_group(
    group_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    journal = glsvc.get_by_group(db, company_id, group_id)
    return {"journal": journal}


@router.get("/gl/posted")
async def gl_list_posted(
    limit: int = Query(100, ge=1, le=500),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    return {"journals": glsvc.list_posted(db, company_id, limit=limit)}


@router.get("/gl/for-report")
async def gl_for_report(
    date_from: str = Query(...),
    date_to: str = Query(...),
    limit: int = Query(500, ge=1, le=2000),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    journals = glsvc.list_for_report(db, company_id, date_from, date_to, limit=limit)
    return {"journals": journals}


@router.patch("/gl/{journal_id}")
async def gl_patch(
    journal_id: str,
    payload: GlPatchRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    jdate = None
    if payload.journal_date:
        try:
            jdate = datetime.fromisoformat(payload.journal_date.replace("Z", "+00:00"))
        except ValueError:
            jdate = datetime.strptime(payload.journal_date[:10], "%Y-%m-%d")
    lines_patch = None
    if payload.lines is not None:
        lines_patch = [ln.model_dump(exclude_none=True) for ln in payload.lines]
    try:
        journal = glsvc.update_draft(
            db,
            company_id,
            journal_id,
            jdate,
            lines_patch,
            payload.balancing_account_code,
            payload.deleted_line_ids,
        )
        return glsvc.journal_to_dict(db, journal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gl/{journal_id}/post")
async def gl_post(
    journal_id: str,
    payload: GlPostRequest = Body(default_factory=GlPostRequest),
    company_id: str = Depends(get_current_company_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        journal = glsvc.post_journal(
            db,
            company_id,
            journal_id,
            _user_id(user),
            confirm_bank_create=payload.confirm_bank_create,
        )
        return glsvc.journal_to_dict(db, journal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gl/unpost")
async def gl_unpost(
    payload: GlUnpostRequest,
    company_id: str = Depends(get_current_company_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        journal = glsvc.unpost_journal_to_draft(
            db, company_id, payload.journal_id, _user_id(user)
        )
        return glsvc.journal_to_dict(db, journal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/gl/{journal_id}/reverse-draft")
async def gl_reverse_draft(
    journal_id: str,
    company_id: str = Depends(get_current_company_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        journal = glsvc.create_reversal_draft(
            db, company_id, journal_id, _user_id(user)
        )
        return glsvc.journal_to_dict(db, journal)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/gl/draft-by-group/{group_id}")
async def gl_delete_draft_by_group(
    group_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    deleted = glsvc.delete_draft_for_group(db, company_id, group_id)
    return {"deleted": deleted}


@router.post("/gl/{journal_id}/sync-lines-to-transactions")
async def gl_sync_lines(
    journal_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        sync = glsvc.sync_draft_journal_lines_to_transactions(db, company_id, journal_id)
        journal = (
            db.query(GlJournal)
            .filter(GlJournal.id == journal_id, GlJournal.company_id == company_id)
            .first()
        )
        if not journal:
            raise ValueError("Journal not found")
        return {
            "sync": sync,
            "journal": glsvc.journal_to_dict(db, journal),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

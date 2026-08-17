"""CRUD and validation for OCR-linked draft journals (no reconciliation groups)."""
from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.ocr_journal import OcrJournal, OcrJournalLine, OcrJournalStatus


def _normalize_source(raw: str) -> str:
    s = (raw or "").strip().lower()
    if s not in ("bank", "ledger"):
        raise ValueError("source must be 'bank' or 'ledger'")
    return s


def _parse_journal_date(raw: str | datetime | None) -> datetime:
    if raw is None:
        raise ValueError("journal_date is required")
    if isinstance(raw, datetime):
        return raw
    s = str(raw).strip()
    if not s:
        raise ValueError("journal_date is required")
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return datetime.fromisoformat(s[:10] + "T12:00:00")
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _validate_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not lines:
        raise ValueError("At least one journal line is required")
    out: list[dict[str, Any]] = []
    total_dr = 0.0
    total_cr = 0.0
    for i, ln in enumerate(lines):
        code = str(ln.get("account_code") or "").strip()
        if not code:
            raise ValueError(f"Line {i + 1}: account_code is required")
        dr = float(ln.get("debit") or 0)
        cr = float(ln.get("credit") or 0)
        if dr < 0 or cr < 0:
            raise ValueError(f"Line {i + 1}: debit and credit must be non-negative")
        if dr > 0 and cr > 0:
            raise ValueError(f"Line {i + 1}: cannot have both debit and credit")
        memo = ln.get("memo")
        out.append(
            {
                "account_code": code,
                "debit": dr,
                "credit": cr,
                "memo": (str(memo).strip()[:500] if memo is not None else None),
            }
        )
        total_dr += dr
        total_cr += cr
    if abs(total_dr - total_cr) > 1e-6:
        raise ValueError(f"Journal is not balanced: debit {total_dr:.2f} vs credit {total_cr:.2f}")
    return out


def _next_voucher_no(db: Session, company_id: str) -> str:
    n = db.query(OcrJournal).filter(OcrJournal.company_id == company_id).count()
    return f"OCR-{n + 1:06d}"


def journal_to_dict(db: Session, j: OcrJournal) -> dict[str, Any]:
    lines = (
        db.query(OcrJournalLine)
        .filter(OcrJournalLine.journal_id == j.id)
        .order_by(OcrJournalLine.line_no.asc())
        .all()
    )
    return {
        "id": j.id,
        "company_id": j.company_id,
        "task_id": j.task_id,
        "source": j.source,
        "source_txn_id": j.source_txn_id,
        "status": j.status.value if hasattr(j.status, "value") else str(j.status),
        "journal_date": j.journal_date.isoformat() if j.journal_date else None,
        "currency": j.currency,
        "voucher_no": j.voucher_no,
        "narration": j.narration,
        "created_at": j.created_at.isoformat() if j.created_at else None,
        "updated_at": j.updated_at.isoformat() if j.updated_at else None,
        "lines": [
            {
                "id": ln.id,
                "line_no": ln.line_no,
                "account_code": ln.account_code,
                "debit": ln.debit,
                "credit": ln.credit,
                "memo": ln.memo,
            }
            for ln in lines
        ],
    }


def get_journal(
    db: Session,
    company_id: str,
    source: str,
    source_txn_id: str,
) -> OcrJournal | None:
    src = _normalize_source(source)
    tid = (source_txn_id or "").strip()
    if not tid:
        return None
    return (
        db.query(OcrJournal)
        .filter(
            OcrJournal.company_id == company_id,
            OcrJournal.source == src,
            OcrJournal.source_txn_id == tid,
        )
        .first()
    )


def list_journals_for_task(db: Session, company_id: str, task_id: str) -> list[OcrJournal]:
    return (
        db.query(OcrJournal)
        .filter(OcrJournal.company_id == company_id, OcrJournal.task_id == task_id)
        .order_by(OcrJournal.journal_date.asc(), OcrJournal.voucher_no.asc())
        .all()
    )


def upsert_journal(
    db: Session,
    company_id: str,
    source: str,
    source_txn_id: str,
    *,
    task_id: str | None,
    journal_date: str | datetime | None,
    narration: str | None,
    voucher_no: str | None,
    currency: str | None,
    lines: list[dict[str, Any]],
) -> OcrJournal:
    src = _normalize_source(source)
    tid = (source_txn_id or "").strip()
    if not tid:
        raise ValueError("source_txn_id is required")

    validated = _validate_lines(lines)
    jd = _parse_journal_date(journal_date)
    cur = get_journal(db, company_id, src, tid)

    if cur is None:
        vn = (voucher_no or "").strip() or _next_voucher_no(db, company_id)
        cur = OcrJournal(
            id=str(uuid.uuid4()),
            company_id=company_id,
            task_id=(task_id.strip() if task_id else None) or None,
            source=src,
            source_txn_id=tid,
            status=OcrJournalStatus.DRAFT,
            journal_date=jd,
            currency=(currency or "HKD").strip()[:8] or "HKD",
            voucher_no=vn[:64],
            narration=(narration or "").strip() or None,
        )
        db.add(cur)
        db.flush()
    else:
        if task_id and task_id.strip():
            cur.task_id = task_id.strip()
        cur.journal_date = jd
        if currency:
            cur.currency = currency.strip()[:8] or cur.currency
        if narration is not None:
            cur.narration = (narration or "").strip() or None
        if voucher_no and voucher_no.strip():
            cur.voucher_no = voucher_no.strip()[:64]
        db.query(OcrJournalLine).filter(OcrJournalLine.journal_id == cur.id).delete(synchronize_session=False)
        db.flush()

    for i, row in enumerate(validated):
        db.add(
            OcrJournalLine(
                id=str(uuid.uuid4()),
                journal_id=cur.id,
                line_no=i + 1,
                account_code=row["account_code"],
                debit=row["debit"],
                credit=row["credit"],
                memo=row.get("memo"),
            )
        )
    db.flush()
    db.refresh(cur)
    return cur


def delete_journal(db: Session, company_id: str, source: str, source_txn_id: str) -> bool:
    j = get_journal(db, company_id, _normalize_source(source), source_txn_id)
    if not j:
        return False
    db.delete(j)
    db.flush()
    return True


def export_journals_json(
    db: Session,
    company_id: str,
    *,
    task_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> list[dict[str, Any]]:
    q = db.query(OcrJournal).filter(OcrJournal.company_id == company_id)
    if task_id:
        q = q.filter(OcrJournal.task_id == task_id)
    if date_from is not None:
        q = q.filter(OcrJournal.journal_date >= date_from)
    if date_to is not None:
        q = q.filter(OcrJournal.journal_date <= date_to)
    rows = q.order_by(OcrJournal.journal_date.asc(), OcrJournal.voucher_no.asc()).all()
    return [journal_to_dict(db, j) for j in rows]


def export_journals_csv(
    db: Session,
    company_id: str,
    *,
    task_id: str | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
) -> str:
    data = export_journals_json(db, company_id, task_id=task_id, date_from=date_from, date_to=date_to)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(
        [
            "voucher_no",
            "journal_date",
            "currency",
            "source",
            "source_txn_id",
            "task_id",
            "line_no",
            "account_code",
            "debit",
            "credit",
            "memo",
            "narration",
        ]
    )
    for j in data:
        narr = j.get("narration") or ""
        for ln in j.get("lines") or []:
            w.writerow(
                [
                    j.get("voucher_no"),
                    (j.get("journal_date") or "")[:10],
                    j.get("currency"),
                    j.get("source"),
                    j.get("source_txn_id"),
                    j.get("task_id"),
                    ln.get("line_no"),
                    ln.get("account_code"),
                    ln.get("debit"),
                    ln.get("credit"),
                    ln.get("memo") or "",
                    narr,
                ]
            )
    return buf.getvalue()

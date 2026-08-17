"""Draft OCR journals: one journal per bank or ledger transaction row."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id
from app.database import get_db
from app.services import ocr_journal_service as ojsvc

router = APIRouter()


class OcrJournalLineIn(BaseModel):
    account_code: str
    debit: float = 0.0
    credit: float = 0.0
    memo: str | None = None


class OcrJournalUpsertBody(BaseModel):
    task_id: str | None = None
    journal_date: str | datetime
    narration: str | None = None
    voucher_no: str | None = None
    currency: str | None = Field(default=None, max_length=8)
    lines: list[OcrJournalLineIn]


def _parse_opt_date(s: str | None) -> datetime | None:
    if not s or not str(s).strip():
        return None
    raw = str(s).strip()
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        return datetime.fromisoformat(raw[:10] + "T00:00:00")
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


# Static paths must be registered before /{source}/{source_txn_id}


@router.get("/export/data")
async def export_ocr_journals(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    task_id: str | None = Query(default=None),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    export_format: Literal["json", "csv"] = Query(default="json", alias="format"),
):
    df = _parse_opt_date(date_from)
    dt = _parse_opt_date(date_to)
    if export_format == "csv":
        text = ojsvc.export_journals_csv(db, company_id, task_id=task_id, date_from=df, date_to=dt)
        return PlainTextResponse(text, media_type="text/csv; charset=utf-8")
    data = ojsvc.export_journals_json(db, company_id, task_id=task_id, date_from=df, date_to=dt)
    return {"journals": data}


@router.get("/by-task/{task_id}")
async def list_ocr_journals_by_task(
    task_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    rows = ojsvc.list_journals_for_task(db, company_id, task_id)
    return {"journals": [ojsvc.journal_to_dict(db, j) for j in rows]}


@router.put("/{source}/{source_txn_id}")
async def upsert_ocr_journal(
    source: Literal["bank", "ledger"],
    source_txn_id: str,
    body: OcrJournalUpsertBody,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        j = ojsvc.upsert_journal(
            db,
            company_id,
            source,
            source_txn_id,
            task_id=body.task_id,
            journal_date=body.journal_date,
            narration=body.narration,
            voucher_no=body.voucher_no,
            currency=body.currency,
            lines=[ln.model_dump() for ln in body.lines],
        )
        db.commit()
        return ojsvc.journal_to_dict(db, j)
    except ValueError as e:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(e)) from e


@router.get("/{source}/{source_txn_id}")
async def get_ocr_journal(
    source: Literal["bank", "ledger"],
    source_txn_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    j = ojsvc.get_journal(db, company_id, source, source_txn_id)
    if not j:
        raise HTTPException(status_code=404, detail="Journal not found")
    return ojsvc.journal_to_dict(db, j)


@router.delete("/{source}/{source_txn_id}")
async def delete_ocr_journal(
    source: Literal["bank", "ledger"],
    source_txn_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    ok = ojsvc.delete_journal(db, company_id, source, source_txn_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Journal not found")
    db.commit()
    return {"status": "deleted"}

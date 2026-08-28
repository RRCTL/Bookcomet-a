"""Bank statement upload and parsing API."""
import asyncio
import logging
import os
import tempfile
import time
import uuid
from datetime import datetime
from threading import Lock
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user
from app.models.identity import User
from app.database import SessionLocal, get_db
from app.models.chat import ChatTask
from app.models.company_context import CompanyProfile
from app.models.transaction import BankTransaction, TransactionStatus
from app.services.bank_opening_row import is_balance_forward_opening_row
from app.services.bank_statement_parser import BankStatementParser
from app.services.file_storage import assert_file_type, assert_upload_size


def _assert_upload_payload(filename: str | None, content: bytes) -> None:
    """Size + type gate; map ValueError to HTTPException."""
    try:
        assert_upload_size(content)
        assert_file_type(filename or "upload.bin", content)
    except ValueError as exc:
        detail = str(exc)
        code = 413 if "maximum size" in detail.lower() else 400
        raise HTTPException(status_code=code, detail=detail) from exc



class PageCountResponse(BaseModel):
    page_count: int


class BankUploadStartResponse(BaseModel):
    job_id: str
    status: str


class BankUploadStatusResponse(BaseModel):
    job_id: str
    filename: str
    status: str
    progress_percent: int
    label: str
    page_current: int
    page_total: int
    error: Optional[str]
    result: Optional[Any]
    page_verification: dict[str, str] = Field(default_factory=dict)


class BankUploadActiveRow(BaseModel):
    """In-flight bank upload job for company-wide workspace activity (polling)."""
    job_id: str
    task_id: str
    company_id: str
    owner_user_id: Optional[str]
    filename: str
    status: str
    progress_percent: int
    label: str
    page_current: int
    page_total: int
    page_verification: dict[str, str] = Field(default_factory=dict)


logger = logging.getLogger(__name__)

router = APIRouter()
_JOB_LOCK = Lock()
_UPLOAD_JOBS: dict[str, dict[str, Any]] = {}
_JOB_TTL_SECONDS = 60 * 60


class _BankUploadCancelled(Exception):
    pass


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


def _is_bf_opening_row(txn: dict[str, Any]) -> bool:
    return is_balance_forward_opening_row(txn)


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
    import re as _re

    _mon = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }
    _m1 = _re.match(r"^(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})$", text, _re.IGNORECASE)
    if _m1:
        mon = _mon.get(_m1.group(2).lower()[:3])
        if mon:
            try:
                return datetime(int(_m1.group(3)), mon, int(_m1.group(1)))
            except ValueError:
                pass
    _m2 = _re.match(r"^([A-Za-z]{3,9})\s+(\d{4})$", text, _re.IGNORECASE)
    if _m2:
        mon = _mon.get(_m2.group(1).lower()[:3])
        if mon:
            try:
                return datetime(int(_m2.group(2)), mon, 1)
            except ValueError:
                pass
    return None


def _cleanup_old_jobs() -> None:
    now = time.time()
    stale_ids: list[str] = []
    with _JOB_LOCK:
        for job_id, payload in _UPLOAD_JOBS.items():
            created_at = float(payload.get("created_at_ts") or 0)
            if now - created_at > _JOB_TTL_SECONDS:
                stale_ids.append(job_id)
        for job_id in stale_ids:
            _UPLOAD_JOBS.pop(job_id, None)


def list_active_bank_upload_jobs_for_company(company_id: str) -> list[dict[str, Any]]:
    """Non-terminal bank upload jobs for company-wide workspace polling (in-process memory only)."""
    with _JOB_LOCK:
        rows: list[dict[str, Any]] = []
        for jid, state in _UPLOAD_JOBS.items():
            if state.get("company_id") != company_id:
                continue
            st = str(state.get("status") or "")
            if st in ("completed", "failed", "cancelled"):
                continue
            rows.append(
                {
                    "job_id": jid,
                    "task_id": str(state.get("task_id") or ""),
                    "company_id": str(state.get("company_id") or company_id),
                    "owner_user_id": state.get("owner_user_id"),
                    "filename": str(state.get("filename") or ""),
                    "status": st,
                    "progress_percent": int(state.get("progress_percent") or 0),
                    "label": str(state.get("label") or ""),
                    "page_current": int(state.get("page_current") or 0),
                    "page_total": max(1, int(state.get("page_total") or 1)),
                    "page_verification": dict(state.get("page_verification") or {}),
                }
            )
        return rows


def _is_upload_job_cancelled(job_id: str) -> bool:
    with _JOB_LOCK:
        return str(_UPLOAD_JOBS.get(job_id, {}).get("status") or "") == "cancelled"


def _load_company_identity(db: Session, company_id: str) -> dict[str, Any]:
    profile = (
        db.query(CompanyProfile)
        .filter(CompanyProfile.company_id == company_id)
        .first()
    )
    profile_settings = profile.custom_settings if profile and isinstance(profile.custom_settings, dict) else {}
    fallback_keywords = profile_settings.get("company_name_keywords")
    return {
        "company_name": (
            (profile.company_name if profile else None)
            or (profile_settings.get("company_name") if isinstance(profile_settings.get("company_name"), str) else None)
        ),
        "company_name_keywords": (
            profile.company_name_keywords
            if profile and isinstance(profile.company_name_keywords, list)
            else (fallback_keywords if isinstance(fallback_keywords, list) else [])
        ),
    }


def _txn_date_raw(txn: dict[str, Any]) -> object:
    return txn.get("日期") or txn.get("date") or txn.get("transaction_date") or txn.get("bank_date")


def _persist_bank_transactions(
    db: Session,
    company_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    batch_id = str(uuid.uuid4())
    stored_count = 0
    voucher_seq_by_date: dict[str, int] = {}

    raw = result.get("transactions") or []
    n = len(raw)
    next_after: list[datetime | None] = [None] * n
    prev_before: list[datetime | None] = [None] * n
    nxt: datetime | None = None
    for i in range(n - 1, -1, -1):
        next_after[i] = nxt
        t = raw[i]
        if isinstance(t, dict):
            d = _parse_date(_txn_date_raw(t))
            if d is not None:
                nxt = d
    prv: datetime | None = None
    for i in range(n):
        prev_before[i] = prv
        t = raw[i]
        if isinstance(t, dict):
            d = _parse_date(_txn_date_raw(t))
            if d is not None:
                prv = d

    for i, txn in enumerate(raw):
        if not isinstance(txn, dict):
            continue
        date_value = _txn_date_raw(txn)
        bank_date = _parse_date(date_value)
        bf_fallback = False
        if bank_date is None and _is_bf_opening_row(txn):
            bank_date = next_after[i] or prev_before[i]
            bf_fallback = bank_date is not None
        deposit = txn.get("存入") or txn.get("received") or txn.get("deposit")
        withdrawal = txn.get("提取") or txn.get("spent") or txn.get("withdrawal")
        amount = _parse_amount(deposit) if deposit not in (None, "", 0) else _parse_amount(withdrawal)
        if amount is None and _is_bf_opening_row(txn):
            # Opening balance rows have no Dr/Cr movement; store zero so the row persists.
            amount = 0.0
        if bank_date is None or amount is None:
            continue
        if bf_fallback:
            iso = bank_date.strftime("%Y-%m-%d")
            txn["transaction_date"] = iso
            txn["bank_date"] = iso
            txn["date"] = iso

        description = txn.get("備註") or txn.get("description") or txn.get("description_raw") or ""
        date_key = bank_date.strftime("%y%m")
        voucher_seq_by_date[date_key] = voucher_seq_by_date.get(date_key, 0) + 1
        generated_voucher_no = f"BR-{date_key}-{voucher_seq_by_date[date_key]:03d}"
        reference = generated_voucher_no
        currency = txn.get("幣別") or txn.get("currency") or "HKD"
        account_category = txn.get("categorise") or txn.get("分類") or txn.get("category") or txn.get("account_category")

        bank_txn = BankTransaction(
            id=str(uuid.uuid4()),
            company_id=company_id,
            account_id=result.get("bank") or "UNKNOWN",
            bank_date=bank_date,
            amount=amount,
            currency=currency,
            description_raw=str(description),
            description_norm=str(description).lower(),
            account_category=str(account_category).strip() if account_category else None,
            reference=reference,
            import_batch_id=batch_id,
            status=TransactionStatus.UNRECONCILED,
        )
        db.add(bank_txn)
        txn["憑證號"] = reference
        txn["reference"] = reference
        txn["db_id"] = bank_txn.id
        txn["import_batch_id"] = batch_id
        stored_count += 1

    if stored_count:
        db.commit()

    for txn in raw:
        if not isinstance(txn, dict):
            continue
        tid = str(txn.get("db_id") or "").strip()
        if not tid:
            continue
        try:
            from app.services import gl_journal_service as glsvc

            glsvc.ensure_draft_for_txn(db, company_id, bank_txn_id=tid)
        except Exception:
            logger.exception("GL ensure_draft_for_txn failed for bank %s", tid)

    out = {
        "bank": result["bank"],
        "transactions": result["transactions"],
        "count": result["count"],
        "pages_processed": result.get("pages_processed", 1),
        "transactions_per_page": result.get("transactions_per_page", {}),
        "avg_transactions_per_page": result.get("avg_transactions_per_page", 0),
        "ocr_preview_text": result.get("ocr_preview_text", ""),
        "ocr_preview_source": result.get("ocr_preview_source", "unknown"),
        "import_batch_id": batch_id,
        "stored_count": stored_count,
    }
    pv = result.get("page_verification")
    if isinstance(pv, dict) and pv:
        out["page_verification"] = {str(k): str(v) for k, v in pv.items()}
    return out


async def _run_upload_job(job_id: str, tmp_path: str, suffix: str, company_id: str) -> None:
    db = SessionLocal()
    try:
        company_identity = _load_company_identity(db, company_id)
    finally:
        db.close()

    try:
        parser = BankStatementParser()
        file_type = suffix[1:] if suffix.startswith(".") else suffix

        def on_progress(payload: dict[str, Any]) -> None:
            with _JOB_LOCK:
                state = _UPLOAD_JOBS.get(job_id)
                if not state:
                    return
                if state.get("status") == "cancelled":
                    raise _BankUploadCancelled()
                state["status"] = "processing"
                state["progress_percent"] = int(payload.get("percent", state.get("progress_percent", 0)))
                state["label"] = str(payload.get("label", state.get("label", "處理中")))
                page_total = payload.get("page_total")
                page_current = payload.get("page_current")
                if page_total is not None:
                    state["page_total"] = max(1, int(page_total))
                if page_current is not None:
                    state["page_current"] = max(0, int(page_current))
                pv = payload.get("page_verification")
                if isinstance(pv, dict):
                    acc = state.setdefault("page_verification", {})
                    for k, v in pv.items():
                        if isinstance(k, str) and isinstance(v, str):
                            acc[k] = v
                state["updated_at_ts"] = time.time()

        on_progress({"percent": 5, "label": "後端開始處理", "page_current": 0, "page_total": 1})
        result = await parser.parse_statement(
            tmp_path,
            file_type,
            company_identity=company_identity,
            progress_callback=on_progress,
        )

        if _is_upload_job_cancelled(job_id):
            raise _BankUploadCancelled()

        db = SessionLocal()
        try:
            if _is_upload_job_cancelled(job_id):
                raise _BankUploadCancelled()
            response_payload = _persist_bank_transactions(db, company_id, result)
        finally:
            db.close()

        with _JOB_LOCK:
            state = _UPLOAD_JOBS.get(job_id)
            if state:
                if state.get("status") == "cancelled":
                    return
                pv_final = response_payload.get("page_verification")
                if isinstance(pv_final, dict) and pv_final:
                    state["page_verification"] = {
                        str(k): str(v) for k, v in pv_final.items()
                    }
                state.update(
                    {
                        "status": "completed",
                        "progress_percent": 100,
                        "label": "BANK OCR + AI 處理完成",
                        "page_current": int(response_payload.get("pages_processed", 1)),
                        "page_total": int(response_payload.get("pages_processed", 1)),
                        "result": response_payload,
                        "updated_at_ts": time.time(),
                    }
                )
    except _BankUploadCancelled:
        with _JOB_LOCK:
            state = _UPLOAD_JOBS.get(job_id)
            if state:
                state.update(
                    {
                        "status": "cancelled",
                        "progress_percent": 100,
                        "label": "已取消",
                        "error": "Cancelled by user",
                        "updated_at_ts": time.time(),
                    }
                )
    except Exception as exc:
        logger.error("Bank statement async job failed: %s", exc, exc_info=True)
        with _JOB_LOCK:
            state = _UPLOAD_JOBS.get(job_id)
            if state:
                state.update(
                    {
                        "status": "failed",
                        "progress_percent": 100,
                        "label": "處理失敗",
                        "error": str(exc),
                        "updated_at_ts": time.time(),
                    }
                )
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                logger.warning("Failed to clean up temp upload file: %s", tmp_path)


@router.post("/bank-statements/page-count", response_model=PageCountResponse)
async def get_bank_statement_page_count(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
):
    """
    Return page count for uploaded statement file.
    For non-PDF files, page count is 1.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix != ".pdf":
        content = await file.read()
        _assert_upload_payload(file.filename, content)
        return {"page_count": 1}

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode='wb') as tmp:
        content = await file.read()
        _assert_upload_payload(file.filename, content)
        tmp.write(content)
        tmp_path = tmp.name

    try:
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:
            raise HTTPException(
                status_code=500,
                detail="PyMuPDF is required for PDF page count"
            ) from exc

        doc = fitz.open(tmp_path)
        page_count = len(doc)
        doc.close()
        return {"page_count": max(1, int(page_count))}
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Failed to detect PDF page count: %s", exc)
        return {"page_count": 1}
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                logger.warning("Failed to clean up temp page-count file: %s", tmp_path)

@router.post("/bank-statements/upload")
async def upload_bank_statement(
    file: UploadFile = File(...),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db)
):
    """
    Upload and parse bank statement (CSV/Excel/PDF)
    
    Returns:
        {
            'bank': 'BOC' | 'HSBC' | 'BEA' | 'HANG_SENG' | 'SCB' | 'DBS' | 'UNKNOWN',
            'transactions': [...],
            'count': int
        }
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    
    logger.info("Received bank statement upload: %s", file.filename)

    suffix = os.path.splitext(file.filename)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb") as tmp:
        content = await file.read()
        _assert_upload_payload(file.filename, content)
        tmp.write(content)
        tmp_path = tmp.name

    try:
        parser = BankStatementParser()
        file_type = suffix[1:]  # Remove dot (e.g., '.pdf' -> 'pdf')
        company_identity = _load_company_identity(db, company_id)
        result = await parser.parse_statement(
            tmp_path,
            file_type,
            company_identity=company_identity,
        )
        logger.info("Successfully parsed %s transactions from %s", result["count"], result["bank"])
        return _persist_bank_transactions(db, company_id, result)
        
    except Exception as e:
        logger.error(f"Bank statement parsing failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to parse bank statement: {str(e)}"
        )
    finally:
        # Clean up temp file
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
                logger.info(f"Cleaned up temp file: {tmp_path}")
            except Exception as e:
                logger.warning(f"Failed to clean up temp file {tmp_path}: {e}")


@router.post("/bank-statements/upload/start", response_model=BankUploadStartResponse)
async def start_upload_bank_statement_job(
    file: UploadFile = File(...),
    task_id: str = Form(...),
    company_id: str = Depends(get_current_company_id),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    tid = (task_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="task_id is required")
    task_exists = (
        db.query(ChatTask.id)
        .filter(
            ChatTask.id == tid,
            ChatTask.company_id == company_id,
            ChatTask.deleted_at.is_(None),
        )
        .first()
    )
    if not task_exists:
        raise HTTPException(status_code=404, detail="Task not found")

    _cleanup_old_jobs()
    suffix = os.path.splitext(file.filename)[1].lower()
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb") as tmp:
        content = await file.read()
        _assert_upload_payload(file.filename, content)
        tmp.write(content)
        tmp_path = tmp.name

    job_id = str(uuid.uuid4())
    with _JOB_LOCK:
        _UPLOAD_JOBS[job_id] = {
            "job_id": job_id,
            "company_id": company_id,
            "task_id": tid,
            "owner_user_id": user.id,
            "filename": file.filename,
            "status": "queued",
            "progress_percent": 0,
            "label": "已加入處理隊列",
            "page_current": 0,
            "page_total": 1,
            "error": None,
            "result": None,
            "page_verification": {},
            "created_at_ts": time.time(),
            "updated_at_ts": time.time(),
        }

    asyncio.create_task(_run_upload_job(job_id, tmp_path, suffix, company_id))
    return {"job_id": job_id, "status": "queued"}


@router.get("/bank-statements/upload/active", response_model=list[BankUploadActiveRow])
async def list_active_bank_upload_jobs(
    company_id: str = Depends(get_current_company_id),
    user: User = Depends(get_current_user),
):
    """Company-wide: any member may list in-flight bank upload jobs (same company)."""
    _ = user
    rows = list_active_bank_upload_jobs_for_company(company_id)
    return [BankUploadActiveRow.model_validate(r) for r in rows]


@router.post("/bank-statements/upload/cancel/{job_id}", response_model=BankUploadStatusResponse)
async def cancel_upload_bank_statement_job(
    job_id: str,
    company_id: str = Depends(get_current_company_id),
    user: User = Depends(get_current_user),
):
    _ = user
    with _JOB_LOCK:
        state = _UPLOAD_JOBS.get(job_id)
        if not state:
            raise HTTPException(status_code=404, detail="Job not found")
        if state.get("company_id") != company_id:
            raise HTTPException(status_code=404, detail="Job not found")
        if state.get("status") not in ("completed", "failed", "cancelled"):
            state.update(
                {
                    "status": "cancelled",
                    "progress_percent": 100,
                    "label": "已取消",
                    "error": "Cancelled by user",
                    "updated_at_ts": time.time(),
                }
            )
        return {
            "job_id": state["job_id"],
            "filename": state["filename"],
            "status": state["status"],
            "progress_percent": state["progress_percent"],
            "label": state["label"],
            "page_current": state.get("page_current", 0),
            "page_total": state.get("page_total", 1),
            "error": state.get("error"),
            "result": state.get("result"),
            "page_verification": state.get("page_verification") or {},
        }


@router.get("/bank-statements/upload/status/{job_id}", response_model=BankUploadStatusResponse)
async def get_upload_bank_statement_job_status(
    job_id: str,
    company_id: str = Depends(get_current_company_id),
    user: User = Depends(get_current_user),
):
    _ = user
    with _JOB_LOCK:
        state = _UPLOAD_JOBS.get(job_id)
        if not state:
            raise HTTPException(status_code=404, detail="Job not found")
        if state.get("company_id") != company_id:
            raise HTTPException(status_code=404, detail="Job not found")
        return {
            "job_id": state["job_id"],
            "filename": state["filename"],
            "status": state["status"],
            "progress_percent": state["progress_percent"],
            "label": state["label"],
            "page_current": state.get("page_current", 0),
            "page_total": state.get("page_total", 1),
            "error": state.get("error"),
            "result": state.get("result"),
            "page_verification": state.get("page_verification") or {},
        }

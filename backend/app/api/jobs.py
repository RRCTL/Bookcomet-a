"""
Durable background jobs for OCR and AI chat (client can poll after disconnect).
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.ai_chat import AIChatRequest
from app.api.deps import get_current_company_id, get_current_user, get_trace_id
from app.database import get_db
from app.models.background_job import BackgroundJob
from app.models.chat import ChatTask
from app.models.identity import User
from app.services.abuse_guard import check_monthly_cost, check_chat_rate_async, validate_chat_message
from app.services.job_tasks import (
    cleanup_expired_ocr_job_uploads,
    ocr_job_upload_still_available,
    persist_ocr_job_upload_retention,
    run_ai_chat_background_job,
    run_coa_deploy_background_job,
    run_ocr_background_job,
    save_ocr_job_file,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


class JobCreateResponse(BaseModel):
    job_id: str
    status: str


class CoaDeployBatchItem(BaseModel):
    task_id: str
    batch_id: str
    run_id: str
    transactions: list[dict[str, Any]]
    base_payload: dict[str, Any] | None = None
    table_preset: str | None = None


class CoaDeployJobRequest(BaseModel):
    mode: str
    batches: list[CoaDeployBatchItem]


class JobStatusResponse(BaseModel):
    id: str
    job_type: str
    status: str
    result_json: dict[str, Any] | None = None
    error_text: str | None = None
    task_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    progress_percent: int | None = None
    progress_label: str | None = None
    original_filename: str | None = None
    storage_retained_until: str | None = None
    ocr_retry_eligible: bool = False


def _parse_progress_int(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        v = int(float(raw.strip()))
        return max(0, min(100, v))
    except ValueError:
        return None


def _default_progress_label(job: BackgroundJob) -> str | None:
    st = job.status
    if st == "queued":
        if job.job_type == "coa_deploy":
            return "Deploy queued"
        return "排隊中"
    if st == "running":
        if job.job_type == "ocr":
            return "OCR 處理中"
        if job.job_type == "ai_chat":
            return "AI 處理中"
        if job.job_type == "coa_deploy":
            return "Deploying account codes"
    if st == "cancelled":
        return "已取消"
    return None


def _original_filename_from_job(job: BackgroundJob) -> str | None:
    rj = job.request_json
    if not isinstance(rj, dict):
        return None
    fn = rj.get("original_filename")
    if isinstance(fn, str) and fn.strip():
        return fn.strip()
    return None


def _job_to_out(job: BackgroundJob) -> JobStatusResponse:
    pct = _parse_progress_int(job.progress_percent)
    label = _default_progress_label(job)
    rj = job.request_json if isinstance(job.request_json, dict) else {}
    retained = rj.get("storage_retained_until")
    retained_s = retained.strip() if isinstance(retained, str) and retained.strip() else None
    retry_ok = bool(job.job_type == "ocr" and ocr_job_upload_still_available(rj))
    return JobStatusResponse(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        result_json=job.result_json if isinstance(job.result_json, dict) else None,
        error_text=job.error_text,
        task_id=job.task_id,
        created_at=job.created_at.isoformat() if job.created_at else None,
        updated_at=job.updated_at.isoformat() if job.updated_at else None,
        progress_percent=pct,
        progress_label=label,
        original_filename=_original_filename_from_job(job),
        storage_retained_until=retained_s,
        ocr_retry_eligible=retry_ok,
    )


def _ensure_task_in_company(db: Session, task_id: str | None, company_id: str) -> None:
    tid = (task_id or "").strip()
    if not tid:
        return
    exists = (
        db.query(ChatTask.id)
        .filter(
            ChatTask.id == tid,
            ChatTask.company_id == company_id,
            ChatTask.deleted_at.is_(None),
        )
        .first()
    )
    if not exists:
        raise HTTPException(status_code=404, detail="Task not found")


@router.post("/ocr", response_model=JobCreateResponse, status_code=202)
async def create_ocr_job(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    processing_mode: Optional[str] = Form("AR"),
    multi_receipt_confirmed: bool = Form(False),
    multi_receipt_acknowledged: bool = Form(False),
    force_process: bool = Form(False),
    ap_vlm_receipt_signal: Optional[str] = Form(None),
    ap_vlm_table_preset: Optional[str] = Form(None),
    task_id: Optional[str] = Form(None),
    company_id: str = Depends(get_current_company_id),
    trace_id: str = Depends(get_trace_id),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobCreateResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    import os

    suffix = os.path.splitext(file.filename)[1].lower()
    supported_images = [".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"]
    supported_pdf = [".pdf"]
    is_pdf = suffix in supported_pdf
    is_image = suffix in supported_images
    if not is_pdf and not is_image:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file format: {suffix}",
        )
    # Lazy import avoids loading the full OCR stack when only listing jobs.
    from app.api import ocr as ocr_module

    if is_pdf and not ocr_module.PDF_SUPPORT:
        raise HTTPException(status_code=500, detail="PDF support not available")

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        from app.services.file_storage import assert_file_type, assert_upload_size

        assert_upload_size(content)
        assert_file_type(file.filename, content)
    except ValueError as exc:
        detail = str(exc)
        code = 413 if "maximum size" in detail.lower() else 400
        raise HTTPException(status_code=code, detail=detail) from exc

    _cost_ok, _cost_msg = check_monthly_cost(db, company_id)
    if not _cost_ok:
        raise HTTPException(status_code=429, detail=_cost_msg)

    _ensure_task_in_company(db, task_id, company_id)

    cleanup_expired_ocr_job_uploads()

    job_id = str(uuid.uuid4())
    storage_path = save_ocr_job_file(job_id, content, file.filename)
    job = BackgroundJob(
        id=job_id,
        company_id=company_id,
        owner_user_id=user.id,
        job_type="ocr",
        status="queued",
        trace_id=trace_id,
        task_id=task_id,
        request_json={
            "storage_path": storage_path,
            "original_filename": file.filename,
            "processing_mode": processing_mode or "AR",
            "multi_receipt_confirmed": multi_receipt_confirmed,
            "multi_receipt_acknowledged": multi_receipt_acknowledged,
            "force_process": force_process,
            "ap_vlm_receipt_signal": (ap_vlm_receipt_signal or "").strip().lower() or None,
            "ap_vlm_table_preset": (ap_vlm_table_preset or "").strip().lower() or None,
        },
    )
    db.add(job)
    db.commit()
    background_tasks.add_task(run_ocr_background_job, job_id)
    return JobCreateResponse(job_id=job_id, status="queued")


@router.post("/ai-chat", response_model=JobCreateResponse, status_code=202)
async def create_ai_chat_job(
    background_tasks: BackgroundTasks,
    body: AIChatRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobCreateResponse:
    mode = body.mode.upper()
    _ocr_review_mode = mode in ("AR", "AP", "BANK", "OTHER", "RECON")
    rate_ok, rate_msg = await check_chat_rate_async(company_id, ocr_mode=_ocr_review_mode)
    if not rate_ok:
        raise HTTPException(status_code=429, detail=rate_msg)
    cleaned_message, msg_err = validate_chat_message(body.message)
    if msg_err:
        raise HTTPException(status_code=400, detail=msg_err)
    body = body.model_copy(update={"message": cleaned_message})

    _cost_ok, _cost_msg = check_monthly_cost(db, company_id)
    if not _cost_ok:
        raise HTTPException(status_code=429, detail=_cost_msg)

    task_id = body.session_id.rsplit("_", 1)[0]
    _ensure_task_in_company(db, task_id, company_id)

    job_id = str(uuid.uuid4())
    job = BackgroundJob(
        id=job_id,
        company_id=company_id,
        owner_user_id=user.id,
        job_type="ai_chat",
        status="queued",
        trace_id=None,
        task_id=task_id,
        request_json=body.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    background_tasks.add_task(run_ai_chat_background_job, job_id)
    return JobCreateResponse(job_id=job_id, status="queued")


@router.post("/coa-deploy", response_model=JobCreateResponse, status_code=202)
async def create_coa_deploy_job(
    background_tasks: BackgroundTasks,
    body: CoaDeployJobRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobCreateResponse:
    mode = (body.mode or "AR").upper()
    if mode not in ("AR", "AP", "BANK"):
        raise HTTPException(status_code=400, detail="mode must be AR, AP, or BANK")
    if not body.batches:
        raise HTTPException(status_code=400, detail="At least one batch is required")

    rate_ok, rate_msg = await check_chat_rate_async(company_id)
    if not rate_ok:
        raise HTTPException(status_code=429, detail=rate_msg)
    cost_ok, cost_msg = check_monthly_cost(db, company_id)
    if not cost_ok:
        raise HTTPException(status_code=429, detail=cost_msg)

    task_ids = {b.task_id.strip() for b in body.batches if b.task_id.strip()}
    for tid in task_ids:
        _ensure_task_in_company(db, tid, company_id)

    job_id = str(uuid.uuid4())
    first_task = body.batches[0].task_id.strip() if body.batches else None
    job = BackgroundJob(
        id=job_id,
        company_id=company_id,
        owner_user_id=user.id,
        job_type="coa_deploy",
        status="queued",
        trace_id=None,
        task_id=first_task or None,
        request_json={
            "mode": mode,
            "batches": [b.model_dump(mode="json") for b in body.batches],
        },
    )
    db.add(job)
    db.commit()
    background_tasks.add_task(run_coa_deploy_background_job, job_id)
    return JobCreateResponse(job_id=job_id, status="queued")


@router.get("/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobStatusResponse:
    job = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.id == job_id,
            BackgroundJob.company_id == company_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _ = user
    return _job_to_out(job)


@router.post("/{job_id}/ocr-retry-page", response_model=JobStatusResponse)
async def retry_ocr_job_failed_page(
    job_id: str,
    page: int = Query(..., ge=1),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobStatusResponse:
    """Re-run OCR for one failed PDF page (Scenario D). Requires retained upload file."""
    from app.api.ocr import merge_ocr_job_retry_page_result
    from app.api import ocr as ocr_module

    if not ocr_module.PDF_SUPPORT:
        raise HTTPException(status_code=500, detail="PDF support not available")

    job = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.id == job_id,
            BackgroundJob.company_id == company_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _ = user
    if job.job_type != "ocr":
        raise HTTPException(status_code=400, detail="Not an OCR job")
    if job.status != "completed":
        raise HTTPException(status_code=400, detail="Job must be completed")

    meta = job.request_json if isinstance(job.request_json, dict) else {}
    path = meta.get("storage_path")
    if not ocr_job_upload_still_available(meta):
        raise HTTPException(
            status_code=400,
            detail="Original upload no longer available for retry",
        )

    result = job.result_json
    if not isinstance(result, dict):
        raise HTTPException(status_code=400, detail="No OCR result to patch")
    if result.get("document_type") != "multi_page_pdf":
        raise HTTPException(
            status_code=400,
            detail="Retry is only supported for multi-page PDF results",
        )

    total_pages = int(result.get("total_pages") or 0)
    if total_pages < 1 or page > total_pages:
        raise HTTPException(status_code=400, detail="Invalid page number")

    processing_mode = str(meta.get("processing_mode") or result.get("processing_mode") or "AR")
    multi_receipt_confirmed = bool(meta.get("multi_receipt_confirmed"))
    filename = str(meta.get("original_filename") or "upload.pdf")
    trace_id = str(result.get("trace_id") or job.trace_id or "")

    _cost_ok, _cost_msg = check_monthly_cost(db, company_id)
    if not _cost_ok:
        raise HTTPException(status_code=429, detail=_cost_msg)

    try:
        updated = await merge_ocr_job_retry_page_result(
            existing=result,
            pdf_path=str(path),
            page_num=page,
            processing_mode=processing_mode,
            multi_receipt_confirmed=multi_receipt_confirmed,
            company_id=company_id,
            trace_id=trace_id,
            filename=filename,
            db=db,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        logger.exception("OCR retry page failed for job %s page %s", job_id, page)
        raise HTTPException(status_code=500, detail="OCR retry failed") from None

    job.result_json = updated
    if isinstance(path, str) and path:
        try:
            persist_ocr_job_upload_retention(job_id, path)
        except Exception:
            logger.exception("Failed to extend OCR upload retention for job %s", job_id)
    db.commit()
    db.refresh(job)
    logger.info("[ocr_metrics] ocr_retry_page_ok job_id=%s page=%s", job_id, page)
    return _job_to_out(job)


@router.post("/{job_id}/cancel", response_model=JobStatusResponse)
async def cancel_job(
    job_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> JobStatusResponse:
    job = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.id == job_id,
            BackgroundJob.company_id == company_id,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    _ = user
    if job.status in ("completed", "failed", "cancelled"):
        return _job_to_out(job)
    job.status = "cancelled"
    job.error_text = "Cancelled by user"
    job.progress_percent = "100"
    db.commit()
    db.refresh(job)
    return _job_to_out(job)


@router.get("", response_model=list[JobStatusResponse])
async def list_active_jobs(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[JobStatusResponse]:
    """Company-wide: queued/running jobs for this company (any member may list)."""
    _ = user
    rows = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.company_id == company_id,
            BackgroundJob.status.in_(("queued", "running")),
        )
        .order_by(BackgroundJob.created_at.desc())
        .limit(50)
        .all()
    )
    return [_job_to_out(j) for j in rows]

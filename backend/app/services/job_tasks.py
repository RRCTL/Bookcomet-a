"""
Background execution for OCR and AI chat jobs (survives client disconnect).
Scheduled via FastAPI BackgroundTasks after returning 202 to the client.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.core.db_concurrency import long_running_db_work_slot
from app.database import SessionLocal
from app.models.background_job import BackgroundJob

logger = logging.getLogger(__name__)


def _is_job_cancelled(db: Session, job_id: str) -> bool:
    job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
    return bool(job and job.status == "cancelled")


class OcrBackgroundJobCancelled(Exception):
    """Raised when OCR sees BackgroundJob.status == cancelled (cooperative cancel)."""


def background_job_cancelled(job_id: str) -> bool:
    """True if the background_jobs row is cancelled (short-lived read-only session)."""
    db = SessionLocal()
    try:
        return _is_job_cancelled(db, job_id)
    finally:
        db.close()


def _job_storage_paths(job_id: str) -> str:
    from app.core.config import settings

    base = os.path.join(settings.uploads_dir, "background_jobs", job_id)
    os.makedirs(base, exist_ok=True)
    return base


def _fail_job_with_fresh_session(job_id: str, message: str) -> None:
    db = SessionLocal()
    try:
        _fail_job(db, job_id, message)
    finally:
        db.close()


async def run_ocr_background_job(job_id: str) -> None:
    """Run OCR job without holding one DB connection across the full pipeline."""
    from app.api.ocr import ocr_test_core

    storage_path: str | None = None
    filename = "upload"
    trace_id = ""
    company_id = ""
    meta: dict[str, Any] = {}

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if not job or job.job_type != "ocr":
            return
        if job.status != "queued":
            return

        meta = job.request_json or {}
        storage_path = meta.get("storage_path")
        filename = meta.get("original_filename") or "upload"
        company_id = job.company_id
        trace_id = job.trace_id or str(uuid.uuid4())
        if not storage_path or not os.path.isfile(storage_path):
            raise FileNotFoundError("OCR job file missing on disk")

        job.status = "running"
        job.progress_percent = "12"
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    try:
        with open(storage_path, "rb") as f:
            content = f.read()
        upload = UploadFile(filename=filename, file=BytesIO(content))

        db_check = SessionLocal()
        try:
            if _is_job_cancelled(db_check, job_id):
                return
        finally:
            db_check.close()

        async with long_running_db_work_slot():
            db_check = SessionLocal()
            try:
                if _is_job_cancelled(db_check, job_id):
                    return
            finally:
                db_check.close()
            work_db = SessionLocal()
            try:
                result = await ocr_test_core(
                    file=upload,
                    processing_mode=str(meta.get("processing_mode") or "AR"),
                    multi_receipt_confirmed=bool(meta.get("multi_receipt_confirmed")),
                    multi_receipt_acknowledged=bool(meta.get("multi_receipt_acknowledged")),
                    force_process=bool(meta.get("force_process")),
                    company_id=company_id,
                    trace_id=trace_id,
                    db=work_db,
                    background_job_id=job_id,
                    ap_vlm_receipt_signal=(meta.get("ap_vlm_receipt_signal") or None),
                    ap_vlm_table_preset=(meta.get("ap_vlm_table_preset") or None),
                )
            finally:
                work_db.close()

        db_done = SessionLocal()
        try:
            job = db_done.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
            if job:
                if job.status == "cancelled":
                    return
                job.trace_id = trace_id
                job.progress_percent = "92"
                job.result_json = result
                job.status = "completed"
                job.error_text = None
                db_done.commit()
        finally:
            db_done.close()
    except OcrBackgroundJobCancelled:
        logger.info("[BackgroundJob] OCR job %s stopped (user cancelled)", job_id)
        return
    except HTTPException as he:
        detail = he.detail if isinstance(he.detail, str) else str(he.detail)
        _fail_job_with_fresh_session(job_id, detail)
    except Exception as exc:
        logger.exception("[BackgroundJob] OCR job %s failed", job_id)
        _fail_job_with_fresh_session(job_id, str(exc)[:4000])
    finally:
        if storage_path and os.path.isfile(storage_path):
            from app.core.config import settings

            if settings.ocr_job_upload_retention_hours > 0:
                try:
                    persist_ocr_job_upload_retention(job_id, storage_path)
                except Exception:
                    logger.exception(
                        "[BackgroundJob] Failed to persist OCR upload retention for job %s",
                        job_id,
                    )
            else:
                try:
                    os.remove(storage_path)
                except OSError:
                    pass


async def run_ai_chat_background_job(job_id: str) -> None:
    from app.api.ai_chat import AIChatRequest, ai_chat_core

    company_id = ""
    owner_user_id: str | None = None
    request = None
    bootstrap_error: str | None = None

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if not job or job.job_type != "ai_chat":
            return
        if job.status != "queued":
            return

        raw = job.request_json
        if not isinstance(raw, dict):
            raise ValueError("Invalid ai_chat job payload")

        company_id = job.company_id
        owner_user_id = job.owner_user_id
        job.status = "running"
        job.progress_percent = "12"
        request = AIChatRequest.model_validate(raw)
        db.commit()
    except Exception as exc:
        db.rollback()
        bootstrap_error = str(exc)[:4000]
        logger.exception("[BackgroundJob] AI chat job %s failed (bootstrap)", job_id)
    finally:
        db.close()

    if bootstrap_error:
        _fail_job_with_fresh_session(job_id, bootstrap_error)
        return

    try:
        async with long_running_db_work_slot():
            db_check = SessionLocal()
            try:
                if _is_job_cancelled(db_check, job_id):
                    return
            finally:
                db_check.close()
            work_db = SessionLocal()
            try:
                response = await ai_chat_core(
                    request, work_db, company_id, owner_user_id=owner_user_id
                )
            finally:
                work_db.close()

        db_done = SessionLocal()
        try:
            job = db_done.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
            if job:
                if job.status == "cancelled":
                    return
                job.progress_percent = "92"
                job.result_json = response.model_dump(mode="json")
                job.status = "completed"
                job.error_text = None
                db_done.commit()
        finally:
            db_done.close()
    except Exception as exc:
        logger.exception("[BackgroundJob] AI chat job %s failed", job_id)
        _fail_job_with_fresh_session(job_id, str(exc)[:4000])


def _fail_job(db: Session, job_id: str, message: str) -> None:
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if job:
            if job.status == "cancelled":
                return
            job.status = "failed"
            job.error_text = message
            db.commit()
    except Exception:
        db.rollback()


def save_ocr_job_file(job_id: str, content: bytes, original_filename: str) -> str:
    """Write uploaded bytes to disk; returns absolute storage path."""
    from app.services.file_storage import storage

    safe_suffix = os.path.splitext(original_filename or "")[1].lower() or ".bin"
    return storage.save_job_input(job_id, content, safe_suffix)


def persist_ocr_job_upload_retention(job_id: str, storage_path: str) -> None:
    """Extend request_json so the upload file stays until storage_retained_until (for retry-page)."""
    from app.core.config import settings

    if settings.ocr_job_upload_retention_hours <= 0:
        return
    if not storage_path or not os.path.isfile(storage_path):
        return
    retained_until = (
        datetime.now(timezone.utc) + timedelta(hours=settings.ocr_job_upload_retention_hours)
    ).isoformat()
    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if not job or not isinstance(job.request_json, dict):
            return
        rj = {**job.request_json}
        rj["storage_retained_until"] = retained_until
        rj["retention_reason"] = "ocr_retry_eligible"
        job.request_json = rj
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def cleanup_expired_ocr_job_uploads(max_rows: int = 300) -> int:
    """
    Remove OCR job input files whose retention deadline has passed; clear storage_path on the row.
    Call from new OCR job creation (lightweight GC).
    """
    from app.core.config import settings

    if settings.ocr_job_upload_retention_hours <= 0:
        return 0
    db = SessionLocal()
    purged = 0
    try:
        rows = (
            db.query(BackgroundJob)
            .filter(BackgroundJob.job_type == "ocr")
            .order_by(BackgroundJob.updated_at.desc())
            .limit(max_rows)
            .all()
        )
        now = datetime.now(timezone.utc)
        for job in rows:
            rj = job.request_json
            if not isinstance(rj, dict):
                continue
            path = rj.get("storage_path")
            if not path or not isinstance(path, str):
                continue
            until_s = rj.get("storage_retained_until")
            if not until_s or not isinstance(until_s, str):
                continue
            try:
                until = datetime.fromisoformat(until_s.replace("Z", "+00:00"))
                if until.tzinfo is None:
                    until = until.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if now < until:
                continue
            if os.path.isfile(path):
                try:
                    os.remove(path)
                except OSError:
                    pass
            new_rj = {**rj}
            new_rj["storage_path"] = None
            new_rj["storage_purged_at"] = now.isoformat()
            new_rj.pop("storage_retained_until", None)
            new_rj.pop("retention_reason", None)
            job.request_json = new_rj
            purged += 1
        if purged:
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return purged


def ocr_job_upload_still_available(meta: dict[str, Any] | None) -> bool:
    """True if retry can read the original upload from disk within retention."""
    if not meta or not isinstance(meta, dict):
        return False
    path = meta.get("storage_path")
    if not path or not isinstance(path, str) or not os.path.isfile(path):
        return False
    from app.core.config import settings

    if settings.ocr_job_upload_retention_hours <= 0:
        return False
    until_s = meta.get("storage_retained_until")
    if not until_s or not isinstance(until_s, str):
        return False
    try:
        until = datetime.fromisoformat(until_s.replace("Z", "+00:00"))
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
    except ValueError:
        return False
    return datetime.now(timezone.utc) < until


def _coa_name_by_code(mode: str, db: Session, company_id: str) -> dict[str, str]:
    from app.services.chart_of_accounts import get_chart_of_accounts

    accounts = get_chart_of_accounts(mode, db=db, company_id=company_id)
    out: dict[str, str] = {}
    for item in accounts:
        code = str(item.get("code") or "").strip()
        name = str(item.get("name_en") or "").strip()
        if code and name:
            out[code] = name
    return out


def _txn_dict_to_deploy(txn: dict[str, Any], *, is_bank: bool, default_mode: str) -> "DeployTxn":
    from app.api.reconciliation import DeployTxn

    if is_bank:
        dep = txn.get("deposit")
        wd = txn.get("withdrawal")
        amount = dep if dep not in (None, "") else wd
        return DeployTxn(
            id_number=str(txn.get("id_number") or "") or None,
            date=str(txn.get("date") or "") or None,
            amount=float(amount) if amount not in (None, "") else None,
            payer="",
            payee=str(txn.get("particulars") or "") or None,
            memo=str(txn.get("particulars") or "") or None,
            transaction_type="BANK",
            category="",
        )
    return DeployTxn(
        id_number=str(txn.get("id_number") or "") or None,
        date=str(txn.get("date") or "") or None,
        amount=float(txn.get("amount")) if txn.get("amount") not in (None, "") else None,
        payer=str(txn.get("payer") or "") or None,
        payee=str(txn.get("payee") or "") or None,
        memo=str(txn.get("memo") or "") or None,
        transaction_type=str(txn.get("transaction_type") or default_mode) or default_mode,
        category=str(txn.get("category") or "") or None,
    )


def _results_to_code_map(results: list[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in results:
        if not isinstance(row, dict):
            continue
        idn = str(row.get("id_number") or "")
        code = row.get("suggested_code")
        if idn and code:
            out[idn] = str(code)
    return out


def _apply_codes_to_rows(
    rows: list[dict[str, Any]],
    code_map: dict[str, str],
    *,
    is_bank: bool,
    name_by_code: dict[str, str],
) -> None:
    for row in rows:
        idn = str(row.get("id_number") or "")
        code = code_map.get(idn)
        if not code:
            continue
        row["account_code"] = code
        row["category"] = name_by_code.get(code) or row.get("category") or ""


async def _deploy_codes_for_txns(
    txns: list[dict[str, Any]],
    *,
    mode: str,
    is_bank: bool,
    company_id: str,
    db: Session,
) -> dict[str, str]:
    from app.api.reconciliation import AccountCodeDeployRequest, deploy_account_codes_core

    if not txns:
        return {}
    deploy_txns = [_txn_dict_to_deploy(t, is_bank=is_bank, default_mode=mode) for t in txns]
    if is_bank:
        payload = AccountCodeDeployRequest(transactions=deploy_txns, mode="BANK")
        res = await deploy_account_codes_core(payload, company_id, db)
        return _results_to_code_map(res.get("results") or [])

    ar_txns = [t for t in deploy_txns if (t.transaction_type or "").upper() == "AR"]
    ap_txns = [t for t in deploy_txns if (t.transaction_type or "").upper() == "AP"]
    has_mixed = len(ar_txns) > 0 and len(ap_txns) > 0
    code_map: dict[str, str] = {}
    if has_mixed:
        if ar_txns:
            ar_res = await deploy_account_codes_core(
                AccountCodeDeployRequest(transactions=ar_txns, mode="AR"),
                company_id,
                db,
            )
            code_map.update(_results_to_code_map(ar_res.get("results") or []))
        if ap_txns:
            ap_res = await deploy_account_codes_core(
                AccountCodeDeployRequest(transactions=ap_txns, mode="AP"),
                company_id,
                db,
            )
            code_map.update(_results_to_code_map(ap_res.get("results") or []))
        return code_map

    effective_mode = "AP" if len(ap_txns) > 0 and len(ar_txns) == 0 else mode
    res = await deploy_account_codes_core(
        AccountCodeDeployRequest(transactions=deploy_txns, mode=effective_mode),
        company_id,
        db,
    )
    return _results_to_code_map(res.get("results") or [])


async def run_coa_deploy_background_job(job_id: str) -> None:
    """Deploy Chart of Accounts codes for ERP module batches; persists module snapshots."""
    from app.api.tasks import persist_batch_ocr_snapshot_payload
    from app.models.chat import ChatTask

    company_id = ""
    mode = "AR"
    batches: list[dict[str, Any]] = []
    bootstrap_error: str | None = None

    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if not job or job.job_type != "coa_deploy":
            return
        if job.status != "queued":
            return
        raw = job.request_json
        if not isinstance(raw, dict):
            raise ValueError("Invalid coa_deploy job payload")
        company_id = job.company_id
        mode = str(raw.get("mode") or "AR").upper()
        batch_raw = raw.get("batches")
        if not isinstance(batch_raw, list) or not batch_raw:
            raise ValueError("coa_deploy job requires at least one batch")
        batches = [b for b in batch_raw if isinstance(b, dict)]
        if not batches:
            raise ValueError("coa_deploy job requires at least one batch")
        job.status = "running"
        job.progress_percent = "8"
        db.commit()
    except Exception as exc:
        db.rollback()
        bootstrap_error = str(exc)[:4000]
        logger.exception("[BackgroundJob] CoA deploy job %s failed (bootstrap)", job_id)
    finally:
        db.close()

    if bootstrap_error:
        _fail_job_with_fresh_session(job_id, bootstrap_error)
        return

    is_bank = mode == "BANK"
    saved_batches: list[dict[str, str]] = []
    try:
        async with long_running_db_work_slot():
            db_check = SessionLocal()
            try:
                if _is_job_cancelled(db_check, job_id):
                    return
            finally:
                db_check.close()

            work_db = SessionLocal()
            try:
                name_by_code = _coa_name_by_code(mode, work_db, company_id)
                total = len(batches)
                for idx, batch in enumerate(batches):
                    db_check = SessionLocal()
                    try:
                        if _is_job_cancelled(db_check, job_id):
                            return
                    finally:
                        db_check.close()

                    task_id = str(batch.get("task_id") or "").strip()
                    batch_id = str(batch.get("batch_id") or "").strip()
                    run_id = str(batch.get("run_id") or "").strip()
                    txns_raw = batch.get("transactions")
                    if not task_id or not batch_id:
                        raise ValueError("Each batch requires task_id and batch_id")
                    if not isinstance(txns_raw, list):
                        raise ValueError("Each batch requires transactions list")
                    txns = [dict(t) for t in txns_raw if isinstance(t, dict)]

                    task = (
                        work_db.query(ChatTask)
                        .filter(
                            ChatTask.id == task_id,
                            ChatTask.company_id == company_id,
                            ChatTask.deleted_at.is_(None),
                        )
                        .first()
                    )
                    if not task:
                        raise ValueError(f"Task not found: {task_id}")

                    code_map = await _deploy_codes_for_txns(
                        txns,
                        mode=mode,
                        is_bank=is_bank,
                        company_id=company_id,
                        db=work_db,
                    )
                    _apply_codes_to_rows(txns, code_map, is_bank=is_bank, name_by_code=name_by_code)

                    base_payload = batch.get("base_payload")
                    base = dict(base_payload) if isinstance(base_payload, dict) else {}
                    now_iso = datetime.now(timezone.utc).isoformat()
                    if is_bank:
                        payload = {**base, "bankTransactions": txns, "moduleSavedAt": now_iso}
                    else:
                        payload = {**base, "arapTransactions": txns, "moduleSavedAt": now_iso}
                    persist_batch_ocr_snapshot_payload(work_db, task_id, batch_id, payload)
                    task.updated_at = datetime.now(timezone.utc)
                    work_db.commit()

                    pct = int(((idx + 1) / max(total, 1)) * 88) + 8
                    prog_db = SessionLocal()
                    try:
                        prog_job = prog_db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
                        if prog_job and prog_job.status != "cancelled":
                            prog_job.progress_percent = str(min(96, pct))
                            prog_db.commit()
                    finally:
                        prog_db.close()

                    saved_batches.append({"run_id": run_id, "batch_id": batch_id, "task_id": task_id})
            finally:
                work_db.close()

        db_done = SessionLocal()
        try:
            job = db_done.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
            if job:
                if job.status == "cancelled":
                    return
                job.progress_percent = "100"
                job.result_json = {
                    "mode": mode,
                    "batches": saved_batches,
                    "batch_count": len(saved_batches),
                }
                job.status = "completed"
                job.error_text = None
                db_done.commit()
        finally:
            db_done.close()
    except HTTPException as he:
        detail = he.detail if isinstance(he.detail, str) else str(he.detail)
        _fail_job_with_fresh_session(job_id, detail)
    except Exception as exc:
        logger.exception("[BackgroundJob] CoA deploy job %s failed", job_id)
        _fail_job_with_fresh_session(job_id, str(exc)[:4000])

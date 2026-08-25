"""
Chat Task persistence API.

Provides CRUD for ChatTask records, message history, file storage,
state snapshots, and audit log. All queries are scoped to company_id
(tenant isolation) — no cross-tenant data is ever reachable.
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user
from app.api.ocr import AP_CROSS_VLM_MODEL, ocr_test_core
from app.core.db_concurrency import long_running_db_work_slot
from app.core.text_limits import (
    MAX_TASK_DUP_WARNING_CHARS,
    MAX_TASK_MESSAGE_CONTENT_TYPE_CHARS,
    MAX_TASK_MESSAGE_ROLE_CHARS,
    MAX_TASK_MESSAGE_TEXT_CHARS,
    MAX_TASK_TITLE_CHARS,
)
from app.database import SessionLocal, get_db
from app.models.chat import (
    ChatTask,
    TaskAuditLog,
    TaskFile,
    TaskMessage,
    TaskStateSnapshot,
)
from app.models.identity import Membership, User
from app.models.workflow import WorkflowRun
from app.services.file_storage import assert_file_type, read_stored_bytes, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tasks")


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _task_out(task: ChatTask) -> dict:
    return {
        "id": task.id,
        "company_id": task.company_id,
        "owner_user_id": task.owner_user_id,
        "title": task.title or "",
        "processing_mode": task.processing_mode or "AR",
        "status": task.status or "idle",
        "is_shared_to_company": bool(task.is_shared_to_company),
        "file_count": task.file_count or 0,
        "page_count": task.page_count or 0,
        "has_spreadsheet": bool(task.has_spreadsheet),
        "bank_batch_ids": task.bank_batch_ids,
        "ledger_batch_ids": task.ledger_batch_ids,
        "dup_warning": task.dup_warning,
        "title_generated": bool(task.title_generated),
        "created_at": task.created_at.isoformat() if task.created_at else "",
        "updated_at": task.updated_at.isoformat() if task.updated_at else "",
    }


def _get_task_or_404(task_id: str, company_id: str, db: Session) -> ChatTask:
    """Fetch a task, enforcing company_id tenant isolation on every lookup."""
    task = (
        db.query(ChatTask)
        .filter(
            ChatTask.id == task_id,
            ChatTask.company_id == company_id,
            ChatTask.deleted_at.is_(None),
        )
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


def _check_read_access(task: ChatTask, user: User) -> None:
    if task.owner_user_id != user.id and not task.is_shared_to_company:
        raise HTTPException(status_code=403, detail="Access denied")


def _check_owner(task: ChatTask, user: User) -> None:
    if task.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Only the task owner can perform this action")


def _infer_homogeneous_arap_from_payload(payload: Any) -> Optional[str]:
    """Match frontend inferHomogeneousArapMode: all rows AR or all AP -> 'AR'|'AP', else None."""
    if not isinstance(payload, dict):
        return None
    rows = payload.get("arapTransactions")
    if not isinstance(rows, list) or not rows:
        return None
    types: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            return None
        v = str(raw.get("transaction_type") or "").strip().upper()
        if v not in ("AR", "AP"):
            return None
        types.add(v)
    if len(types) != 1:
        return None
    return "AR" if "AR" in types else "AP"


def _is_company_owner(user_id: str, company_id: str, db: Session) -> bool:
    membership = (
        db.query(Membership)
        .filter(Membership.user_id == user_id, Membership.company_id == company_id)
        .first()
    )
    return membership is not None and membership.role == "owner"


def _write_audit(
    task_id: str,
    user_id: str,
    action: str,
    db: Session,
    request: Optional[Request] = None,
) -> None:
    """Insert-only audit record. Never raises — errors are logged and swallowed."""
    try:
        ip = None
        ua = None
        if request:
            ip = request.client.host if request.client else None
            ua = request.headers.get("user-agent")
        db.add(
            TaskAuditLog(
                id=str(uuid.uuid4()),
                task_id=task_id,
                user_id=user_id,
                action=action,
                ip_address=ip,
                user_agent=ua,
            )
        )
    except Exception as exc:
        logger.warning("[TaskAudit] Failed to write audit record: %s", exc)


def _link_workflow_run_snapshot(db: Session, task_id: str, message_id: str) -> None:
    """Point workflow run(s) for this task at the active OCR snapshot message."""
    for run in db.query(WorkflowRun).filter(WorkflowRun.task_id == task_id).all():
        run.snapshot_message_id = message_id


def _batch_ocr_snapshot_message_id(upload_batch_id: str) -> str:
    return f"ocr-batch-{upload_batch_id.strip()}"


def persist_batch_ocr_snapshot_payload(
    db: Session,
    task_id: str,
    upload_batch_id: str,
    payload_json: dict,
    *,
    role: str = "assistant",
    content_text: str = "OCR snapshot",
) -> TaskMessage:
    """Upsert one batch OCR snapshot row. Caller must commit."""
    batch_id = (upload_batch_id or "").strip()
    if not batch_id:
        raise ValueError("upload_batch_id is required")

    msg_id = _batch_ocr_snapshot_message_id(batch_id)
    msg = (
        db.query(TaskMessage)
        .filter(TaskMessage.id == msg_id, TaskMessage.task_id == task_id)
        .first()
    )
    payload = {**payload_json, "uploadBatchId": batch_id}

    if msg:
        msg.role = role
        msg.content_text = content_text
        msg.content_type = "ocr_snapshot"
        msg.payload_json = payload
    else:
        last = (
            db.query(TaskMessage)
            .filter(TaskMessage.task_id == task_id)
            .order_by(TaskMessage.sequence_index.desc())
            .first()
        )
        next_seq = (last.sequence_index + 1) if last else 0
        msg = TaskMessage(
            id=msg_id,
            task_id=task_id,
            sequence_index=next_seq,
            role=role,
            content_text=content_text,
            content_type="ocr_snapshot",
            payload_json=payload,
        )
        db.add(msg)

    _link_workflow_run_snapshot(db, task_id, msg.id)
    return msg


# ── Request / Response Schemas ─────────────────────────────────────────────

class ChatTaskCreate(BaseModel):
    id: Optional[str] = None          # client may supply its own UUID
    title: str = Field(..., max_length=MAX_TASK_TITLE_CHARS)
    processing_mode: str = Field(..., max_length=64)
    status: str = "idle"
    file_count: int = 0
    page_count: int = 0
    has_spreadsheet: bool = False
    bank_batch_ids: Optional[list] = None
    ledger_batch_ids: Optional[list] = None
    dup_warning: Optional[str] = Field(default=None, max_length=MAX_TASK_DUP_WARNING_CHARS)
    title_generated: bool = False


class ChatTaskPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=MAX_TASK_TITLE_CHARS)
    processing_mode: Optional[str] = Field(default=None, max_length=64)  # AR / AP / BANK / … when user retags task
    status: Optional[str] = None
    file_count: Optional[int] = None
    page_count: Optional[int] = None
    has_spreadsheet: Optional[bool] = None
    bank_batch_ids: Optional[list] = None
    ledger_batch_ids: Optional[list] = None
    dup_warning: Optional[str] = Field(default=None, max_length=MAX_TASK_DUP_WARNING_CHARS)
    title_generated: Optional[bool] = None


class TaskMessageCreate(BaseModel):
    role: str = Field(..., max_length=MAX_TASK_MESSAGE_ROLE_CHARS)  # user / assistant / system
    content_text: str = Field(..., max_length=MAX_TASK_MESSAGE_TEXT_CHARS)
    content_type: str = Field(default="text", max_length=MAX_TASK_MESSAGE_CONTENT_TYPE_CHARS)
    payload_json: Optional[Any] = None


class TaskMessagePatch(BaseModel):
    content_text: Optional[str] = Field(default=None, max_length=MAX_TASK_MESSAGE_TEXT_CHARS)
    payload_json: Optional[Any] = None


class TaskStateSnapshotCreate(BaseModel):
    state_type: str                    # recon_pool | report_data | spreadsheet
    payload_json: Any


class TaskResponse(BaseModel):
    id: str
    company_id: str
    owner_user_id: str
    title: str
    processing_mode: str
    status: str
    is_shared_to_company: bool
    file_count: int
    page_count: int
    has_spreadsheet: bool
    bank_batch_ids: Optional[Any]
    ledger_batch_ids: Optional[Any]
    dup_warning: Optional[str]
    title_generated: bool
    created_at: str
    updated_at: str


class TaskMessageResponse(BaseModel):
    id: str
    task_id: str
    sequence_index: int
    role: str
    content_text: str
    content_type: str
    payload_json: Optional[Any]
    created_at: str


class TaskDeleteResponse(BaseModel):
    status: str
    deleted_id: str


class TaskShareResponse(BaseModel):
    is_shared_to_company: bool


class TaskStateResponse(BaseModel):
    id: str
    task_id: str
    state_type: str
    payload_json: Optional[Any]
    version: int
    created_at: str


class ApCrossVerifyRequest(BaseModel):
    file_ids: list[str] = Field(..., min_length=1)
    multi_receipt_confirmed: bool = False
    multi_receipt_acknowledged: bool = False
    force_process: bool = False


# ── Phase 1: Task CRUD ─────────────────────────────────────────────────────

@router.get("", response_model=list[TaskResponse])
def list_tasks(
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """List tasks visible to this user within the company."""
    is_owner = _is_company_owner(user.id, company_id, db)

    query = db.query(ChatTask).filter(
        ChatTask.company_id == company_id,
        ChatTask.deleted_at.is_(None),
    )

    if not is_owner:
        query = query.filter(
            or_(
                ChatTask.owner_user_id == user.id,
                ChatTask.is_shared_to_company.is_(True),
            )
        )

    tasks = query.order_by(ChatTask.updated_at.desc()).all()
    _write_audit("_list_", user.id, "listed", db, request)
    db.commit()
    return [_task_out(t) for t in tasks]


@router.post("", response_model=TaskResponse, status_code=201)
def create_task(
    body: ChatTaskCreate,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Create a new task. The client may provide its own ID to keep frontend/backend IDs in sync."""
    task_id = body.id or str(uuid.uuid4())

    # Idempotency: if the same ID is submitted again, return the existing task
    existing = db.query(ChatTask).filter(ChatTask.id == task_id).first()
    if existing:
        if existing.company_id != company_id or existing.deleted_at is not None:
            raise HTTPException(status_code=409, detail="Task ID is not available")
        _check_read_access(existing, user)
        return _task_out(existing)

    task = ChatTask(
        id=task_id,
        company_id=company_id,
        owner_user_id=user.id,
        title=body.title,
        processing_mode=body.processing_mode,
        status=body.status,
        file_count=body.file_count,
        page_count=body.page_count,
        has_spreadsheet=body.has_spreadsheet,
        bank_batch_ids=body.bank_batch_ids,
        ledger_batch_ids=body.ledger_batch_ids,
        dup_warning=body.dup_warning,
        title_generated=body.title_generated,
    )
    db.add(task)
    _write_audit(task_id, user.id, "created", db, request)
    db.commit()
    db.refresh(task)
    return _task_out(task)


@router.get("/{task_id}", response_model=TaskResponse)
def get_task(
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)
    _write_audit(task_id, user.id, "viewed", db, request)
    db.commit()
    return _task_out(task)


@router.patch("/{task_id}", response_model=TaskResponse)
def patch_task(
    task_id: str,
    body: ChatTaskPatch,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)

    updates = body.model_dump(exclude_none=True)
    # Retagging AR ↔ AP matches OCR edits; collaborators can PATCH only this pair (same as ocr_snapshot).
    if updates and set(updates.keys()) <= {"processing_mode"}:
        new_mode = str(updates.get("processing_mode") or "").strip().upper()
        old_mode = (task.processing_mode or "").strip().upper()
        if new_mode in ("AR", "AP") and old_mode in ("AR", "AP"):
            _check_read_access(task, user)
        else:
            _check_owner(task, user)
    else:
        _check_owner(task, user)

    for field, value in updates.items():
        setattr(task, field, value)
    task.updated_at = _now()
    db.commit()
    db.refresh(task)
    return _task_out(task)


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)
    _check_owner(task, user)
    task.deleted_at = _now()
    _write_audit(task_id, user.id, "deleted", db, request)
    db.commit()


# ── Phase 2: Message sub-routes ────────────────────────────────────────────

@router.get("/{task_id}/messages", response_model=list[TaskMessageResponse])
def get_messages(
    task_id: str,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)
    messages = (
        db.query(TaskMessage)
        .filter(TaskMessage.task_id == task_id)
        .order_by(TaskMessage.sequence_index)
        .all()
    )
    return [
        {
            "id": m.id,
            "task_id": m.task_id,
            "sequence_index": m.sequence_index,
            "role": m.role,
            "content_text": m.content_text,
            "content_type": m.content_type,
            "payload_json": m.payload_json,
            "created_at": m.created_at.isoformat() if m.created_at else "",
        }
        for m in messages
    ]


@router.post("/{task_id}/messages", status_code=201, response_model=TaskMessageResponse)
def append_message(
    task_id: str,
    body: TaskMessageCreate,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)

    # Determine next sequence index
    last = (
        db.query(TaskMessage)
        .filter(TaskMessage.task_id == task_id)
        .order_by(TaskMessage.sequence_index.desc())
        .first()
    )
    next_seq = (last.sequence_index + 1) if last else 0

    msg = TaskMessage(
        id=str(uuid.uuid4()),
        task_id=task_id,
        sequence_index=next_seq,
        role=body.role,
        content_text=body.content_text,
        content_type=body.content_type,
        payload_json=body.payload_json,
    )
    db.add(msg)
    if (body.content_type or "").strip() == "ocr_snapshot":
        inferred = _infer_homogeneous_arap_from_payload(body.payload_json)
        pm = (task.processing_mode or "").strip().upper()
        if inferred and pm in ("AR", "AP") and pm != inferred:
            task.processing_mode = inferred
    task.updated_at = _now()
    _write_audit(task_id, user.id, "message_added", db, request)
    db.commit()
    db.refresh(msg)
    return {
        "id": msg.id,
        "task_id": msg.task_id,
        "sequence_index": msg.sequence_index,
        "role": msg.role,
        "content_text": msg.content_text,
        "content_type": msg.content_type,
        "payload_json": msg.payload_json,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
    }


@router.patch("/{task_id}/messages/{message_id}", status_code=200, response_model=TaskMessageResponse)
def patch_task_message(
    task_id: str,
    message_id: str,
    body: TaskMessagePatch,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Update one persisted message (ocr_snapshot only) without affecting sibling snapshots."""
    if body.content_text is None and body.payload_json is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of content_text or payload_json is required",
        )
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)
    msg = (
        db.query(TaskMessage)
        .filter(TaskMessage.id == message_id, TaskMessage.task_id == task_id)
        .first()
    )
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if (msg.content_type or "").strip() != "ocr_snapshot":
        raise HTTPException(
            status_code=400,
            detail="Only ocr_snapshot messages can be updated with this endpoint",
        )
    if body.content_text is not None:
        msg.content_text = body.content_text
    if body.payload_json is not None:
        msg.payload_json = body.payload_json
    inferred = _infer_homogeneous_arap_from_payload(msg.payload_json)
    pm = (task.processing_mode or "").strip().upper()
    if inferred and pm in ("AR", "AP") and pm != inferred:
        task.processing_mode = inferred
    task.updated_at = _now()
    _link_workflow_run_snapshot(db, task_id, msg.id)
    _write_audit(task_id, user.id, "message_patched", db, request)
    db.commit()
    db.refresh(msg)
    return {
        "id": msg.id,
        "task_id": msg.task_id,
        "sequence_index": msg.sequence_index,
        "role": msg.role,
        "content_text": msg.content_text,
        "content_type": msg.content_type,
        "payload_json": msg.payload_json,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
    }


@router.put("/{task_id}/messages/ocr_snapshot", status_code=200, response_model=TaskMessageResponse)
def upsert_ocr_snapshot(
    task_id: str,
    body: TaskMessageCreate,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Replace (delete + insert) the single OCR spreadsheet snapshot for a task.

    Using a dedicated upsert rather than append avoids accumulating duplicate
    snapshot rows every time a new file batch is processed for the same task.
    """
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)

    # Remove existing snapshot(s) — there should be at most one, but clean all.
    db.query(TaskMessage).filter(
        TaskMessage.task_id == task_id,
        TaskMessage.content_type == "ocr_snapshot",
    ).delete(synchronize_session=False)

    # Determine the sequence index to place the new snapshot after all other rows.
    last = (
        db.query(TaskMessage)
        .filter(TaskMessage.task_id == task_id)
        .order_by(TaskMessage.sequence_index.desc())
        .first()
    )
    next_seq = (last.sequence_index + 1) if last else 0

    msg = TaskMessage(
        id=str(uuid.uuid4()),
        task_id=task_id,
        sequence_index=next_seq,
        role=body.role,
        content_text=body.content_text,
        content_type="ocr_snapshot",
        payload_json=body.payload_json,
    )
    db.add(msg)

    inferred = _infer_homogeneous_arap_from_payload(body.payload_json)
    pm = (task.processing_mode or "").strip().upper()
    if inferred and pm in ("AR", "AP") and pm != inferred:
        task.processing_mode = inferred

    task.updated_at = _now()
    _link_workflow_run_snapshot(db, task_id, msg.id)
    _write_audit(task_id, user.id, "ocr_snapshot_saved", db, request)
    db.commit()
    db.refresh(msg)
    return {
        "id": msg.id,
        "task_id": msg.task_id,
        "sequence_index": msg.sequence_index,
        "role": msg.role,
        "content_text": msg.content_text,
        "content_type": msg.content_type,
        "payload_json": msg.payload_json,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
    }


@router.put(
    "/{task_id}/messages/ocr_snapshot/batch/{upload_batch_id}",
    status_code=200,
    response_model=TaskMessageResponse,
)
def upsert_batch_ocr_snapshot(
    task_id: str,
    upload_batch_id: str,
    body: TaskMessageCreate,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Upsert one OCR snapshot for an upload batch without deleting sibling batch snapshots."""
    batch_id = (upload_batch_id or "").strip()
    if not batch_id:
        raise HTTPException(status_code=400, detail="upload_batch_id is required")

    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)

    payload = body.payload_json if isinstance(body.payload_json, dict) else {}
    msg = persist_batch_ocr_snapshot_payload(
        db,
        task_id,
        batch_id,
        payload,
        role=body.role,
        content_text=body.content_text,
    )

    inferred = _infer_homogeneous_arap_from_payload(msg.payload_json)
    pm = (task.processing_mode or "").strip().upper()
    if inferred and pm in ("AR", "AP") and pm != inferred:
        task.processing_mode = inferred

    task.updated_at = _now()
    _link_workflow_run_snapshot(db, task_id, msg.id)
    _write_audit(task_id, user.id, "ocr_snapshot_saved", db, request)
    db.commit()
    db.refresh(msg)
    return {
        "id": msg.id,
        "task_id": msg.task_id,
        "sequence_index": msg.sequence_index,
        "role": msg.role,
        "content_text": msg.content_text,
        "content_type": msg.content_type,
        "payload_json": msg.payload_json,
        "created_at": msg.created_at.isoformat() if msg.created_at else "",
    }


@router.post("/{task_id}/ap-cross-verify")
async def ap_cross_verify(
    task_id: str,
    body: ApCrossVerifyRequest,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Re-run AP OCR on stored task files: primary VLM plus in-place cross-VLM merge when configured."""
    if not AP_CROSS_VLM_MODEL:
        raise HTTPException(
            status_code=503,
            detail="AP_CROSS_VLM_MODEL is not configured. Set it in the environment to enable Double check.",
        )

    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)
    if (task.processing_mode or "").strip().upper() != "AP":
        raise HTTPException(
            status_code=400,
            detail="Task processing_mode must be AP for cross-VLM verify.",
        )

    task_files: list[TaskFile] = []
    for fid in body.file_ids:
        tf = (
            db.query(TaskFile)
            .filter(
                TaskFile.id == fid,
                TaskFile.task_id == task_id,
                TaskFile.deleted_at.is_(None),
            )
            .first()
        )
        if not tf:
            raise HTTPException(status_code=404, detail=f"Task file not found: {fid}")
        task_files.append(tf)

    results_out: list[dict[str, Any]] = []

    async with long_running_db_work_slot():
        for tf in task_files:
            storage_path = Path(tf.storage_path)
            if not storage_path.is_file():
                raise HTTPException(status_code=404, detail=f"File missing on disk: {tf.id}")
            content = read_stored_bytes(storage_path)
            if not content:
                raise HTTPException(status_code=400, detail=f"Empty file: {tf.original_filename or tf.id}")

            fname = tf.original_filename or f"{tf.id}.bin"
            upload = UploadFile(filename=fname, file=BytesIO(content))
            trace_id = str(uuid.uuid4())

            work_db = SessionLocal()
            try:
                ocr_result = await ocr_test_core(
                    file=upload,
                    processing_mode="AP",
                    multi_receipt_confirmed=body.multi_receipt_confirmed,
                    multi_receipt_acknowledged=body.multi_receipt_acknowledged,
                    force_process=body.force_process,
                    company_id=company_id,
                    trace_id=trace_id,
                    db=work_db,
                    ap_force_cross_verify=True,
                )
            finally:
                work_db.close()

            results_out.append({
                "file_id": tf.id,
                "filename": tf.original_filename or "",
                "result": ocr_result,
            })

    _write_audit(task_id, user.id, "ap_cross_verify", db, request)
    db.commit()

    return {"model": AP_CROSS_VLM_MODEL, "results": results_out}


# ── Phase 3: File sub-routes ───────────────────────────────────────────────

@router.post("/{task_id}/files", status_code=201)
async def upload_file(
    task_id: str,
    file: UploadFile = File(...),
    request: Request = None,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)

    file_uuid = str(uuid.uuid4())
    ext = Path(file.filename or "file").suffix.lower() or ".bin"
    contents = await file.read()
    try:
        assert_file_type(file.filename or f"upload{ext}", contents)
        dest_path = storage.save(company_id, task_id, file_uuid, contents, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    task_file = TaskFile(
        id=file_uuid,
        task_id=task_id,
        original_filename=file.filename,
        storage_path=dest_path,
        file_size_bytes=len(contents),
        mime_type=file.content_type,
    )
    db.add(task_file)
    task.updated_at = _now()
    _write_audit(task_id, user.id, "file_uploaded", db, request)
    db.commit()
    db.refresh(task_file)

    return {
        "id": task_file.id,
        "task_id": task_file.task_id,
        "original_filename": task_file.original_filename,
        "file_size_bytes": task_file.file_size_bytes,
        "mime_type": task_file.mime_type,
        "created_at": task_file.created_at.isoformat() if task_file.created_at else "",
    }


@router.get("/{task_id}/files/{file_id}/download")
def download_file(
    task_id: str,
    file_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    # Re-validate ownership on EVERY download — never trust task_id alone
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)

    task_file = (
        db.query(TaskFile)
        .filter(
            TaskFile.id == file_id,
            TaskFile.task_id == task_id,
            TaskFile.deleted_at.is_(None),
        )
        .first()
    )
    if not task_file:
        raise HTTPException(status_code=404, detail="File not found")

    storage_path = Path(task_file.storage_path)
    if not storage_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    _write_audit(task_id, user.id, "file_downloaded", db, request)
    db.commit()

    return FileResponse(
        path=str(storage_path),
        filename=task_file.original_filename or file_id,
        media_type=task_file.mime_type or "application/octet-stream",
    )


@router.get("/{task_id}/files/{file_id}/receipt-crop")
def receipt_crop_preview(
    task_id: str,
    file_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1, le=500),
    x: float | None = Query(None, ge=0.0, le=1.0),
    y: float | None = Query(None, ge=0.0, le=1.0),
    w: float | None = Query(None, ge=0.0, le=1.0),
    h: float | None = Query(None, ge=0.0, le=1.0),
    bx: int | None = Query(None, description="Pixel bbox x"),
    by: int | None = Query(None, description="Pixel bbox y"),
    bw: int | None = Query(None, description="Pixel bbox w"),
    bh: int | None = Query(None, description="Pixel bbox h"),
):
    """
    On-demand JPEG crop for AQ / Table Review preview.

    Crops from the stored upload using normalized region (preferred) or pixel bbox.
    Does not persist a separate crop file.
    """
    from app.services.receipt_crop_preview import render_receipt_crop_jpeg

    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)

    task_file = (
        db.query(TaskFile)
        .filter(
            TaskFile.id == file_id,
            TaskFile.task_id == task_id,
            TaskFile.deleted_at.is_(None),
        )
        .first()
    )
    if not task_file:
        raise HTTPException(status_code=404, detail="File not found")

    storage_path = Path(task_file.storage_path)
    if not storage_path.exists():
        raise HTTPException(status_code=404, detail="File not found on disk")

    region_norm = None
    region_bbox = None
    if x is not None and y is not None and w is not None and h is not None and w > 0 and h > 0:
        region_norm = {"x": x, "y": y, "w": w, "h": h}
    elif bx is not None and by is not None and bw is not None and bh is not None and bw > 0 and bh > 0:
        region_bbox = {"x": bx, "y": by, "w": bw, "h": bh}

    try:
        jpeg = render_receipt_crop_jpeg(
            storage_path=str(storage_path),
            page=page,
            region_norm=region_norm,
            region_bbox=region_bbox,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found on disk") from None
    except Exception as exc:
        logger.warning("[TaskFiles] receipt-crop failed task=%s file=%s: %s", task_id, file_id, exc)
        raise HTTPException(status_code=422, detail="Could not render receipt crop") from exc

    _write_audit(task_id, user.id, "receipt_crop_previewed", db, request)
    db.commit()

    return Response(
        content=jpeg,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=60"},
    )


@router.delete("/{task_id}/files/{file_id}", status_code=204)
def delete_file(
    task_id: str,
    file_id: str,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)
    _check_owner(task, user)

    task_file = (
        db.query(TaskFile)
        .filter(TaskFile.id == file_id, TaskFile.task_id == task_id)
        .first()
    )
    if not task_file:
        raise HTTPException(status_code=404, detail="File not found")

    # Remove file bytes from disk
    if task_file.storage_path:
        try:
            Path(task_file.storage_path).unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("[TaskFiles] Could not delete file from disk: %s", exc)

    task_file.deleted_at = _now()
    db.commit()


# ── Phase 3: State snapshot sub-routes ────────────────────────────────────

@router.post("/{task_id}/state", status_code=201, response_model=TaskStateResponse)
def save_state_snapshot(
    task_id: str,
    body: TaskStateSnapshotCreate,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)

    # Get next version number for this state_type
    last = (
        db.query(TaskStateSnapshot)
        .filter(
            TaskStateSnapshot.task_id == task_id,
            TaskStateSnapshot.state_type == body.state_type,
        )
        .order_by(TaskStateSnapshot.version.desc())
        .first()
    )
    next_version = (last.version + 1) if last else 1

    snapshot = TaskStateSnapshot(
        id=str(uuid.uuid4()),
        task_id=task_id,
        state_type=body.state_type,
        payload_json=body.payload_json,
        version=next_version,
    )
    db.add(snapshot)
    task.updated_at = _now()
    db.commit()
    db.refresh(snapshot)

    return {
        "id": snapshot.id,
        "task_id": snapshot.task_id,
        "state_type": snapshot.state_type,
        "version": snapshot.version,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else "",
    }


@router.get("/{task_id}/state", response_model=TaskStateResponse)
def get_state_snapshot(
    task_id: str,
    state_type: str,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)

    snapshot = (
        db.query(TaskStateSnapshot)
        .filter(
            TaskStateSnapshot.task_id == task_id,
            TaskStateSnapshot.state_type == state_type,
        )
        .order_by(TaskStateSnapshot.version.desc())
        .first()
    )
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    return {
        "id": snapshot.id,
        "task_id": snapshot.task_id,
        "state_type": snapshot.state_type,
        "payload_json": snapshot.payload_json,
        "version": snapshot.version,
        "created_at": snapshot.created_at.isoformat() if snapshot.created_at else "",
    }


# ── Phase 4: Privacy — Share toggle ───────────────────────────────────────

@router.patch("/{task_id}/share", response_model=TaskShareResponse)
def toggle_share(
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    task = _get_task_or_404(task_id, company_id, db)
    _check_owner(task, user)

    task.is_shared_to_company = not task.is_shared_to_company
    task.updated_at = _now()
    action = "shared" if task.is_shared_to_company else "unshared"
    _write_audit(task_id, user.id, action, db, request)
    db.commit()
    db.refresh(task)
    return {"is_shared_to_company": task.is_shared_to_company}


# ── Phase 4: GDPR — Hard delete ────────────────────────────────────────────

@router.delete("/{task_id}/hard", status_code=204)
def hard_delete_task(
    task_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """
    GDPR right to erasure: zero all message payload_json, remove files from
    disk, then soft-delete the task record itself.
    """
    task = _get_task_or_404(task_id, company_id, db)
    _check_owner(task, user)

    # Zero out message content
    messages = db.query(TaskMessage).filter(TaskMessage.task_id == task_id).all()
    for msg in messages:
        msg.payload_json = None
        msg.content_text = "[deleted]"

    # Remove files from disk
    files = db.query(TaskFile).filter(TaskFile.task_id == task_id).all()
    for f in files:
        if f.storage_path:
            try:
                Path(f.storage_path).unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("[HardDelete] Could not remove file: %s", exc)
        f.deleted_at = _now()

    # Soft-delete the task
    task.deleted_at = _now()
    _write_audit(task_id, user.id, "hard_deleted", db, request)
    db.commit()


# ── Phase 4: PDPO — Data portability export ────────────────────────────────

@router.get("/{task_id}/export")
def export_task(
    task_id: str,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """PDPO right to portability: returns full task data as JSON."""
    task = _get_task_or_404(task_id, company_id, db)
    _check_read_access(task, user)

    messages = (
        db.query(TaskMessage)
        .filter(TaskMessage.task_id == task_id)
        .order_by(TaskMessage.sequence_index)
        .all()
    )
    files = (
        db.query(TaskFile)
        .filter(TaskFile.task_id == task_id, TaskFile.deleted_at.is_(None))
        .all()
    )

    return {
        "task": _task_out(task),
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content_text": m.content_text,
                "content_type": m.content_type,
                "sequence_index": m.sequence_index,
                "created_at": m.created_at.isoformat() if m.created_at else "",
            }
            for m in messages
        ],
        "files": [
            {
                "id": f.id,
                "original_filename": f.original_filename,
                "file_size_bytes": f.file_size_bytes,
                "mime_type": f.mime_type,
                "created_at": f.created_at.isoformat() if f.created_at else "",
            }
            for f in files
        ],
    }

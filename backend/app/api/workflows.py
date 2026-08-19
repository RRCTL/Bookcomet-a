"""
Workflow runs API — Comfy-style graph OCR (sole path for NodeWorkspace UI).
"""
from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import _load_active_jwt_user, get_current_company_id, get_current_user
from app.core.config import default_workflow_provider
from app.graph.default_graphs import build_default_graph
from app.graph.graph_migrate import migrate_all_saved_graphs
from app.graph.graph_schema_v2 import ensure_graph_v2, node_catalog
from app.graph.graph_utils import validate_graph_structure
from app.graph.workflow_skills import (
    get_or_create_skill,
    list_skills,
    reset_skill,
    rollback_skill,
    skill_out,
    update_skill,
)
from app.core.security import decode_access_token
from app.graph.workflow_events import workflow_event_hub
from app.graph.workflow_service import WorkflowService
from app.models.chat import ChatTask, TaskFile
from app.models.identity import User
from datetime import datetime, timezone

from app.models.workflow import (
    WorkflowFolder,
    WorkflowNodeExecution,
    WorkflowPool2Package,
    WorkflowRun,
    WorkflowRunFile,
    WorkflowTemplate,
)
from app.database import get_db
from app.services.file_storage import assert_file_type, storage
from app.services.pool2_storage import pool2

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class CreateRunRequest(BaseModel):
    processing_mode: str = Field(..., description="AR, AP, BANK, OTHER, or RECON")
    template_id: Optional[str] = None


class PatchRunRequest(BaseModel):
    graph_json: dict[str, Any]
    title: Optional[str] = None


class PatchRunMetaRequest(BaseModel):
    folder_id: Optional[str] = None
    clear_folder: bool = False
    archive: Optional[bool] = None
    title: Optional[str] = None
    remove_from_processing: Optional[bool] = None


class FolderCreateRequest(BaseModel):
    name: str
    mode: Optional[str] = None


class FolderPatchRequest(BaseModel):
    name: Optional[str] = None
    sort_order: Optional[int] = None


class ResumeRunRequest(BaseModel):
    approved_payload: dict[str, Any]
    skip_coa: bool = False


class ReVlmRequest(BaseModel):
    task_file_ids: list[str]
    force_process: bool = False
    rescan_reasons: list[str] = Field(default_factory=list)
    rescan_note: Optional[str] = None
    expected_receipt_count: Optional[int] = None


class MoveRunFileBatchRequest(BaseModel):
    upload_batch_id: str = Field(..., min_length=1)


class WorkflowSkillUpdateRequest(BaseModel):
    structured_json: dict[str, Any]


class WorkflowSkillRollbackRequest(BaseModel):
    version: Optional[int] = None


@router.get("/node-catalog")
def get_node_catalog(processing_mode: Optional[str] = None):
    return node_catalog(processing_mode)


@router.get("/skills")
def list_workflow_skills(
    mode: Optional[str] = None,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        rows = list_skills(db, company_id, mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return [skill_out(row, db) for row in rows]


@router.get("/skills/{mode}/{skill_key}")
def get_workflow_skill(
    mode: str,
    skill_key: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        row = get_or_create_skill(db, company_id, mode, skill_key)
        db.commit()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill_out(row, db)


@router.patch("/skills/{mode}/{skill_key}")
def patch_workflow_skill(
    mode: str,
    skill_key: str,
    body: WorkflowSkillUpdateRequest,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        row = get_or_create_skill(db, company_id, mode, skill_key)
        row = update_skill(db, row, body.structured_json, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill_out(row, db)


@router.post("/skills/{mode}/{skill_key}/reset")
def reset_workflow_skill(
    mode: str,
    skill_key: str,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        row = get_or_create_skill(db, company_id, mode, skill_key)
        row = reset_skill(db, row, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill_out(row, db)


@router.post("/skills/{mode}/{skill_key}/rollback")
def rollback_workflow_skill(
    mode: str,
    skill_key: str,
    body: WorkflowSkillRollbackRequest,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        row = get_or_create_skill(db, company_id, mode, skill_key)
        row = rollback_skill(db, row, body.version, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return skill_out(row, db)


def _get_run_or_404(run_id: str, company_id: str, db: Session) -> WorkflowRun:
    run = (
        db.query(WorkflowRun)
        .filter(WorkflowRun.id == run_id, WorkflowRun.company_id == company_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return run


async def _upload_page_count(dest_path: str, ext: str, mime_type: str | None) -> int:
    is_pdf = ext == ".pdf" or (mime_type or "").lower() == "application/pdf"
    if not is_pdf:
        return 1
    try:
        from app.utils.file_converter import pdf_document_page_count

        return max(1, await asyncio.to_thread(pdf_document_page_count, dest_path))
    except Exception:
        return 1


def _run_out(run: WorkflowRun, db: Session) -> dict[str, Any]:
    files = (
        db.query(WorkflowRunFile)
        .filter(WorkflowRunFile.run_id == run.id)
        .all()
    )
    task_files = {
        tf.id: tf
        for tf in db.query(TaskFile)
        .filter(TaskFile.task_id == run.task_id, TaskFile.deleted_at.is_(None))
        .all()
    }
    file_rows = []
    for rf in files:
        tf = task_files.get(rf.task_file_id)
        if not tf:
            continue
        file_rows.append(
            {
                "id": rf.id,
                "task_file_id": rf.task_file_id,
                "file_status": rf.file_status,
                "gate_result": rf.gate_result,
                "error_text": rf.error_text,
                "original_filename": tf.original_filename,
                "page_count": tf.page_count if tf.page_count is not None else 1,
                "upload_batch_id": rf.upload_batch_id,
                "uploaded_at": rf.uploaded_at.isoformat() if rf.uploaded_at else None,
                "batch_committed_at": rf.batch_committed_at.isoformat() if rf.batch_committed_at else None,
                "batch_table_preset": rf.batch_table_preset,
                "batch_receipt_signal": rf.batch_receipt_signal,
                "result_summary_json": rf.result_summary_json,
            }
        )
    return {
        "id": run.id,
        "task_id": run.task_id,
        "company_id": run.company_id,
        "processing_mode": run.processing_mode,
        "title": run.title or "Untitled",
        "run_status": run.run_status,
        "graph_json": run.graph_json,
        "node_states_json": run.node_states_json,
        "console_log_json": run.console_log_json or [],
        "snapshot_message_id": run.snapshot_message_id,
        "folder_id": run.folder_id,
        "archived_at": run.archived_at.isoformat() if run.archived_at else None,
        "processing_removed_at": run.processing_removed_at.isoformat() if run.processing_removed_at else None,
        "files": file_rows,
        "created_at": run.created_at.isoformat() if run.created_at else "",
        "updated_at": run.updated_at.isoformat() if run.updated_at else "",
    }


def _summary_batch_status(files: list[WorkflowRunFile]) -> str:
    statuses = {rf.file_status for rf in files}
    if statuses & {"failed", "warning"}:
        return "failed"
    if statuses and statuses <= {"ok"}:
        return "ok"
    if statuses & {"running"}:
        return "running"
    return "pending"


def _run_summary_batches(files: list[WorkflowRunFile]) -> list[dict[str, Any]]:
    by_batch: dict[str, list[WorkflowRunFile]] = {}
    for rf in files:
        key = rf.upload_batch_id or rf.task_file_id
        by_batch.setdefault(key, []).append(rf)
    batches = []
    for batch_id, batch_files in by_batch.items():
        uploaded_at = min(
            (
                ts
                for ts in [*(rf.uploaded_at for rf in batch_files), *(rf.batch_committed_at for rf in batch_files)]
                if ts is not None
            ),
            default=None,
        )
        batches.append(
            {
                "upload_batch_id": batch_id,
                "status": _summary_batch_status(batch_files),
                "uploaded_at": uploaded_at.isoformat() if uploaded_at else "",
            }
        )
    return sorted(batches, key=lambda b: (b["uploaded_at"], b["upload_batch_id"]))


def _run_summary_out(run: WorkflowRun, db: Session) -> dict[str, Any]:
    files = (
        db.query(WorkflowRunFile)
        .filter(WorkflowRunFile.run_id == run.id)
        .all()
    )
    return {
        "id": run.id,
        "task_id": run.task_id,
        "company_id": run.company_id,
        "processing_mode": run.processing_mode,
        "title": run.title or "Untitled",
        "run_status": run.run_status,
        "file_count": len(files),
        "batches": _run_summary_batches(files),
        "file_statuses": [
            {"task_file_id": rf.task_file_id, "file_status": rf.file_status or "pending"}
            for rf in sorted(files, key=lambda f: (f.uploaded_at or f.created_at or "", f.task_file_id))
        ],
        "folder_id": run.folder_id,
        "archived_at": run.archived_at.isoformat() if run.archived_at else None,
        "processing_removed_at": run.processing_removed_at.isoformat() if run.processing_removed_at else None,
        "created_at": run.created_at.isoformat() if run.created_at else "",
        "updated_at": run.updated_at.isoformat() if run.updated_at else "",
    }


def _node_execution_out(row: WorkflowNodeExecution) -> dict[str, Any]:
    return {
        "id": row.id,
        "node_id": row.node_id,
        "node_type": row.node_type,
        "item_key": row.item_key,
        "status": row.status,
        "error_text": row.error_text,
        "duration_ms": row.duration_ms,
        "provider": row.provider,
        "model": row.model,
        "token_usage_json": row.token_usage_json,
        "has_raw_output": bool(row.storage_path),
        "content_id": row.content_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _template_edge(source: str, target: str, source_handle: str = "out", target_handle: str = "in") -> dict[str, str]:
    return {
        "id": f"{source}-{target}",
        "source": source,
        "target": target,
        "sourceHandle": source_handle,
        "targetHandle": target_handle,
    }


def _template_node(node_id: str, node_type: str, label: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "position": {"x": 0, "y": 0},
        "data": {"label": label, "nodeType": node_type, **(data or {})},
    }


def _plugin_source_id(mode: str) -> str:
    return "receipt" if mode in ("AR", "AP") else "mode"


def _with_double_check(mode: str) -> dict[str, Any]:
    graph = build_default_graph(mode)
    for node in graph["nodes"]:
        if node.get("type") == "VLM_API":
            node.setdefault("data", {})["crossVlm"] = True
    return ensure_graph_v2(graph, mode)


def _with_vote_plugins(mode: str) -> dict[str, Any]:
    graph = build_default_graph(mode)
    source = _plugin_source_id(mode)
    graph["nodes"] = [node for node in graph["nodes"] if node.get("type") != "VLM_API"]
    graph["edges"] = [
        edge
        for edge in graph["edges"]
        if edge.get("source") != "vlm" and edge.get("target") != "vlm"
    ]
    plugin_nodes = [
        _template_node("proposal_a", "VLMProposer", "VLM Proposal A", {"proposalName": "A", "provider": "Qwen", "model": None}),
        _template_node("proposal_b", "VLMProposer", "VLM Proposal B", {"proposalName": "B", "provider": "Qwen", "model": None}),
        _template_node("proposal_c", "VLMProposer", "VLM Proposal C", {"proposalName": "C", "provider": "Qwen", "model": None}),
        _template_node("proposal_pool", "ProposalPoolJoin", "Proposal Pool"),
        _template_node("vlm_judge", "VLMJudge", "VLM Judge", {"provider": default_workflow_provider(), "model": None}),
        _template_node("vote", "VoteSelector", "Vote Selector"),
        _template_node("merge", "MergeResult", "Merge Result"),
    ]
    graph["nodes"].extend(plugin_nodes)
    graph["edges"].extend(
        [
            _template_edge(source, "proposal_a"),
            _template_edge(source, "proposal_b"),
            _template_edge(source, "proposal_c"),
            _template_edge("proposal_a", "proposal_pool"),
            _template_edge("proposal_b", "proposal_pool"),
            _template_edge("proposal_c", "proposal_pool"),
            _template_edge("proposal_pool", "vlm_judge"),
            _template_edge("vlm_judge", "vote"),
            _template_edge("vote", "merge"),
            _template_edge("merge", "table"),
        ]
    )
    return ensure_graph_v2(graph, mode)


def _with_manager_review(mode: str) -> dict[str, Any]:
    graph = build_default_graph(mode)
    graph["edges"] = [
        edge
        for edge in graph["edges"]
        if not (edge.get("source") == "vlm" and edge.get("target") == "table")
    ]
    plugin_nodes = [
        _template_node("manager", "ManagerReview", "Manager Review", {"provider": default_workflow_provider(), "model": None}),
        _template_node("merge", "MergeResult", "Merge Result"),
    ]
    graph["nodes"].extend(plugin_nodes)
    graph["edges"].extend(
        [
            _template_edge("vlm", "manager"),
            _template_edge("manager", "merge"),
            _template_edge("merge", "table"),
        ]
    )
    return ensure_graph_v2(graph, mode)


def _graph_has_vote_proposers(graph_json: dict[str, Any] | None) -> bool:
    if not isinstance(graph_json, dict):
        return False
    nodes = graph_json.get("nodes")
    if not isinstance(nodes, list):
        return False
    return any(isinstance(node, dict) and node.get("type") == "VLMProposer" for node in nodes)


def _vote_template_needs_refresh(graph_json: dict[str, Any] | None) -> bool:
    """True when a stored 3 VLM Vote graph is not the canonical vote-only topology."""
    if _graph_has_vlm_api(graph_json):
        return True
    if not isinstance(graph_json, dict):
        return False
    nodes = graph_json.get("nodes")
    if not isinstance(nodes, list):
        return False
    return any(isinstance(node, dict) and node.get("type") == "ManagerReview" for node in nodes)


def _safe_template_specs(mode: str) -> list[tuple[str, dict[str, Any], bool]]:
    m = (mode or "AP").upper()
    base_name = {
        "AR": "AR Invoice Check",
        "AP": "AP Double Check",
        "BANK": "Bank Statement Verify",
        "OTHER": "Other Review",
        "RECON": "Reconciliation Verify",
    }.get(m, f"{m} Default")
    specs = [(base_name, _with_double_check(m), True)]
    specs.append(("3 VLM Vote", _with_vote_plugins(m), False))
    specs.append(("Manager Review", _with_manager_review(m), False))
    return specs


_VOTE_TEMPLATE_NAMES = frozenset({"3 VLM Vote"})
_MANAGER_REVIEW_TEMPLATE_NAME = "Manager Review"


def _graph_has_vlm_api(graph_json: dict[str, Any] | None) -> bool:
    if not isinstance(graph_json, dict):
        return False
    nodes = graph_json.get("nodes")
    if not isinstance(nodes, list):
        return False
    return any(isinstance(node, dict) and node.get("type") == "VLM_API" for node in nodes)


def _seed_safe_templates(db: Session, company_id: str, user_id: str) -> None:
    modes = ("AR", "AP", "BANK", "OTHER", "RECON")
    existing_rows = (
        db.query(WorkflowTemplate)
        .filter(WorkflowTemplate.company_id == company_id)
        .all()
    )
    existing_by_key = {(tpl.processing_mode, tpl.name): tpl for tpl in existing_rows}
    for mode in modes:
        has_default = (
            db.query(WorkflowTemplate)
            .filter(
                WorkflowTemplate.company_id == company_id,
                WorkflowTemplate.processing_mode == mode,
                WorkflowTemplate.is_default.is_(True),
            )
            .first()
            is not None
        )
        for name, graph_json, is_default in _safe_template_specs(mode):
            key = (mode, name)
            existing = existing_by_key.get(key)
            if existing is not None:
                if name in _VOTE_TEMPLATE_NAMES and _vote_template_needs_refresh(existing.graph_json):
                    existing.graph_json = graph_json
                elif (
                    name == _MANAGER_REVIEW_TEMPLATE_NAME
                    and _graph_has_vote_proposers(existing.graph_json)
                ):
                    existing.graph_json = graph_json
                continue
            db.add(
                WorkflowTemplate(
                    id=str(uuid.uuid4()),
                    company_id=company_id,
                    name=name,
                    processing_mode=mode,
                    graph_json=graph_json,
                    is_default=is_default and not has_default,
                    created_by=user_id,
                )
            )
            if is_default:
                has_default = True
    db.commit()


@router.post("/runs", status_code=201)
def create_run(
    body: CreateRunRequest,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    from app.core.processing_mode import normalize_processing_mode

    mode = normalize_processing_mode(body.processing_mode, "AR")
    if mode not in ("AR", "AP", "BANK", "OTHER", "RECON"):
        raise HTTPException(status_code=400, detail="Invalid processing_mode")
    _seed_safe_templates(db, company_id, user.id)
    template_graph = None
    if body.template_id:
        tpl = (
            db.query(WorkflowTemplate)
            .filter(
                WorkflowTemplate.id == body.template_id,
                WorkflowTemplate.company_id == company_id,
            )
            .first()
        )
        if not tpl:
            raise HTTPException(status_code=404, detail="Template not found")
        template_graph = tpl.graph_json
    else:
        default_tpl = (
            db.query(WorkflowTemplate)
            .filter(
                WorkflowTemplate.company_id == company_id,
                WorkflowTemplate.processing_mode == mode,
                WorkflowTemplate.is_default.is_(True),
            )
            .first()
        )
        if default_tpl:
            template_graph = default_tpl.graph_json
    run = WorkflowService.create_run(
        db,
        company_id=company_id,
        owner_user_id=user.id,
        processing_mode=mode,
        template_graph=template_graph,
    )
    return _run_out(run, db)


@router.get("/runs/{run_id}/audit-json")
def export_run_audit_json(
    run_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    executions = (
        db.query(WorkflowNodeExecution)
        .filter(
            WorkflowNodeExecution.run_id == run.id,
            WorkflowNodeExecution.company_id == company_id,
        )
        .order_by(WorkflowNodeExecution.created_at.asc())
        .all()
    )
    return {
        "run": _run_out(run, db),
        "graph_json": run.graph_json,
        "node_states_json": run.node_states_json or {},
        "console_log_json": run.console_log_json or [],
        "node_executions": [_node_execution_out(row) for row in executions],
        "redaction": {
            "secrets_excluded": True,
            "raw_outputs_download_separately": True,
        },
    }


@router.get("/runs/{run_id}/debug/{execution_id}")
def download_node_debug_output(
    run_id: str,
    execution_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    row = (
        db.query(WorkflowNodeExecution)
        .filter(
            WorkflowNodeExecution.id == execution_id,
            WorkflowNodeExecution.run_id == run.id,
            WorkflowNodeExecution.company_id == company_id,
        )
        .first()
    )
    if not row or not row.storage_path:
        raise HTTPException(status_code=404, detail="Debug output not found")
    path = Path(row.storage_path)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Debug output file not found")
    return FileResponse(path, media_type="application/json", filename=f"{row.node_id}-{row.id}.json")


@router.delete("/runs/{run_id}/debug", status_code=204)
def clear_run_debug_outputs(
    run_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    rows = (
        db.query(WorkflowNodeExecution)
        .filter(
            WorkflowNodeExecution.run_id == run.id,
            WorkflowNodeExecution.company_id == company_id,
            WorkflowNodeExecution.storage_path.isnot(None),
        )
        .all()
    )
    for row in rows:
        row.storage_path = None
    db.commit()


@router.get("/runs")
def list_runs(
    archived: bool = False,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    q = db.query(WorkflowRun).filter(
        WorkflowRun.company_id == company_id,
        WorkflowRun.owner_user_id == user.id,
    )
    if archived:
        q = q.filter(WorkflowRun.archived_at.isnot(None))
    else:
        q = q.filter(WorkflowRun.archived_at.is_(None))
    rows = q.order_by(WorkflowRun.updated_at.desc()).limit(200).all()
    return [_run_summary_out(r, db) for r in rows]


def _websocket_bearer_token(websocket: WebSocket) -> str | None:
    """SEC-CODE-013: read JWT from Authorization header only — never the query string."""
    auth = (websocket.headers.get("authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    return None


async def _websocket_auth_token(websocket: WebSocket) -> str | None:
    header_token = _websocket_bearer_token(websocket)
    if header_token:
        return header_token
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=5.0)
    except Exception:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or payload.get("type") != "auth":
        return None
    token = str(payload.get("token") or "").strip()
    return token or None


@router.websocket("/runs/{run_id}/ws")
async def workflow_run_ws(
    websocket: WebSocket,
    run_id: str,
    db: Session = Depends(get_db),
):
    await websocket.accept()
    token = await _websocket_auth_token(websocket)
    if not token:
        await websocket.close(code=4401)
        return
    try:
        payload = decode_access_token(token)
        user = _load_active_jwt_user(db, payload)
        user_id = user.id
    except HTTPException:
        await websocket.close(code=4401)
        return
    except Exception:
        await websocket.close(code=4401)
        return

    run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
    if not run or run.owner_user_id != user_id:
        await websocket.close(code=4404)
        return

    await workflow_event_hub.connect(run_id, websocket)
    try:
        await workflow_event_hub.snapshot(run_id, run.run_status, run.node_states_json)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await workflow_event_hub.disconnect(run_id, websocket)


@router.get("/runs/{run_id}")
def get_run(
    run_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    return _run_out(run, db)


@router.get("/runs/{run_id}/approved-package")
def get_approved_package(
    run_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    pkg = (
        db.query(WorkflowPool2Package)
        .filter(
            WorkflowPool2Package.run_id == run.id,
            WorkflowPool2Package.company_id == company_id,
        )
        .order_by(WorkflowPool2Package.created_at.desc())
        .first()
    )
    if pkg and pkg.storage_path:
        manifest = pool2.load_node_output(pkg.storage_path)
        if isinstance(manifest, dict):
            return {
                "package_id": pkg.package_id,
                "storage_path": pkg.storage_path,
                "approved_payload": manifest.get("approved_payload"),
                "manifest": manifest,
            }
    states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
    package_id = states.get("pool2_package_id")
    storage_path = states.get("pool2_storage_path")
    if storage_path:
        manifest = pool2.load_node_output(str(storage_path))
        if isinstance(manifest, dict):
            return {
                "package_id": package_id,
                "storage_path": storage_path,
                "approved_payload": manifest.get("approved_payload"),
                "manifest": manifest,
            }
    raise HTTPException(status_code=404, detail="Approved package not found")


@router.patch("/runs/{run_id}")
def patch_run(
    run_id: str,
    body: PatchRunRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    if run.run_status in ("executing", "coa_running"):
        raise HTTPException(status_code=409, detail="Cannot edit graph while running")
    try:
        validate_graph_structure(body.graph_json)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    from app.graph.workflow_path import clear_executed_graph_hash_if_topology_changed

    new_graph = ensure_graph_v2(body.graph_json, run.processing_mode)
    topology_changed = clear_executed_graph_hash_if_topology_changed(run, new_graph)
    run.graph_json = new_graph
    if topology_changed:
        # Switching workflow topology invalidates prior node results; clear them so
        # the canvas shows a clean slate for the new workflow instead of stale cards.
        run.node_states_json = {}
    if body.title is not None:
        run.title = body.title[:500]
        task = db.query(ChatTask).filter(ChatTask.id == run.task_id).first()
        if task:
            task.title = run.title
    db.commit()
    db.refresh(run)
    return _run_out(run, db)


@router.patch("/runs/{run_id}/meta")
def patch_run_meta(
    run_id: str,
    body: PatchRunMetaRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    if body.clear_folder:
        run.folder_id = None
    elif body.folder_id is not None:
        folder = (
            db.query(WorkflowFolder)
            .filter(
                WorkflowFolder.id == body.folder_id,
                WorkflowFolder.company_id == company_id,
            )
            .first()
        )
        if not folder:
            raise HTTPException(status_code=404, detail="Folder not found")
        # Intra-module rule: a run can only land in a folder of its own module.
        if folder.mode and folder.mode.upper() != (run.processing_mode or "").upper():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Cannot move a {run.processing_mode} run into a {folder.mode} folder. "
                    "Runs and results stay within their own module."
                ),
            )
        run.folder_id = folder.id
    if body.archive is True:
        run.archived_at = datetime.now(timezone.utc)
    elif body.archive is False:
        run.archived_at = None
    if body.remove_from_processing is True:
        run.processing_removed_at = datetime.now(timezone.utc)
    elif body.remove_from_processing is False:
        run.processing_removed_at = None
    if body.title is not None:
        run.title = body.title[:500]
        task = db.query(ChatTask).filter(ChatTask.id == run.task_id).first()
        if task:
            task.title = run.title
    db.commit()
    db.refresh(run)
    return _run_out(run, db)


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(
    run_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    WorkflowService.purge_run(db, run)


@router.post("/runs/{run_id}/files", status_code=201)
async def upload_run_file(
    run_id: str,
    file: UploadFile = File(...),
    upload_batch_id: Optional[str] = Form(None),
    uploaded_at: Optional[str] = Form(None),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    file_uuid = str(uuid.uuid4())
    ext = Path(file.filename or "file").suffix.lower() or ".bin"
    contents = await file.read()
    try:
        assert_file_type(file.filename or f"upload{ext}", contents)
        dest_path = storage.save(company_id, run.task_id, file_uuid, contents, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    page_count = await _upload_page_count(dest_path, ext, file.content_type)
    task_file = TaskFile(
        id=file_uuid,
        task_id=run.task_id,
        original_filename=file.filename,
        storage_path=dest_path,
        file_size_bytes=len(contents),
        mime_type=file.content_type,
        page_count=page_count,
    )
    db.add(task_file)
    task = db.query(ChatTask).filter(ChatTask.id == run.task_id).first()
    if task:
        task.file_count = (task.file_count or 0) + 1
    db.flush()
    WorkflowService.sync_run_files(db, run)
    batch_id = (upload_batch_id or "").strip() or str(uuid.uuid4())
    uploaded_ts = datetime.now(timezone.utc)
    if uploaded_at:
        try:
            parsed = datetime.fromisoformat(uploaded_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            uploaded_ts = parsed
        except ValueError:
            pass
    run_file = (
        db.query(WorkflowRunFile)
        .filter(
            WorkflowRunFile.run_id == run.id,
            WorkflowRunFile.task_file_id == file_uuid,
        )
        .first()
    )
    if run_file:
        run_file.upload_batch_id = batch_id
        run_file.uploaded_at = uploaded_ts
    db.commit()
    return {
        "id": task_file.id,
        "original_filename": task_file.original_filename,
        "file_size_bytes": task_file.file_size_bytes,
        "page_count": task_file.page_count,
        "upload_batch_id": batch_id,
        "uploaded_at": uploaded_ts.isoformat(),
    }


@router.post("/runs/{run_id}/execute")
async def execute_run(
    run_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    try:
        run = await WorkflowService.execute_run(db, run)
    except ValueError as exc:
        WorkflowService.recover_stuck_execution(db, run)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception:
        WorkflowService.recover_stuck_execution(db, run)
        raise
    return _run_out(run, db)


@router.post("/runs/{run_id}/rerun")
async def rerun_run(
    run_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Reset files + node states and re-execute the whole workflow graph."""
    run = _get_run_or_404(run_id, company_id, db)
    try:
        run = await WorkflowService.rerun_workflow(db, run)
    except ValueError as exc:
        WorkflowService.recover_stuck_execution(db, run)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception:
        WorkflowService.recover_stuck_execution(db, run)
        raise
    return _run_out(run, db)


@router.post("/runs/{run_id}/recover-stuck")
def recover_stuck_run(
    run_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Reset run/files left in executing/running after a crashed or timed-out execute."""
    run = _get_run_or_404(run_id, company_id, db)
    WorkflowService.recover_stuck_execution(db, run)
    db.refresh(run)
    return _run_out(run, db)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(
    run_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Hard stop: reset run/files/nodes immediately; in-flight OCR aborts on next check."""
    run = _get_run_or_404(run_id, company_id, db)
    run = await WorkflowService.request_run_cancel(db, run)
    return _run_out(run, db)


@router.delete("/runs/{run_id}/files/{task_file_id}", status_code=204)
def delete_run_file(
    run_id: str,
    task_file_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    WorkflowService.remove_run_file(db, run, task_file_id)


@router.patch("/runs/{run_id}/files/{task_file_id}/batch")
def move_run_file_batch(
    run_id: str,
    task_file_id: str,
    body: MoveRunFileBatchRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    WorkflowService.move_run_file_to_batch(
        db,
        run,
        task_file_id,
        body.upload_batch_id,
    )
    db.refresh(run)
    return _run_out(run, db)


@router.post("/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    body: ResumeRunRequest,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    run = await WorkflowService.resume_run(
        db,
        run,
        approved_payload=body.approved_payload,
        skip_coa=body.skip_coa,
        user_id=user.id,
    )
    return _run_out(run, db)


@router.post("/runs/{run_id}/re-vlm")
async def re_vlm_run(
    run_id: str,
    body: ReVlmRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    if not body.task_file_ids:
        raise HTTPException(status_code=400, detail="task_file_ids required")
    run = await WorkflowService.re_vlm_files(
        db,
        run,
        body.task_file_ids,
        force_process=body.force_process,
        rescan_reasons=body.rescan_reasons,
        rescan_note=body.rescan_note,
        expected_receipt_count=body.expected_receipt_count,
    )
    return _run_out(run, db)


@router.post("/runs/{run_id}/files/{task_file_id}/force-process")
async def force_process_file(
    run_id: str,
    task_file_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    run = _get_run_or_404(run_id, company_id, db)
    run = await WorkflowService.re_vlm_files(
        db, run, [task_file_id], force_process=True
    )
    return _run_out(run, db)


# ── Templates (Manager) ───────────────────────────────────────────────────


class TemplateCreateRequest(BaseModel):
    name: str
    processing_mode: str
    graph_json: dict[str, Any]
    is_default: bool = False


class TemplatePatchRequest(BaseModel):
    name: Optional[str] = None
    graph_json: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = None


@router.get("/templates")
def list_templates(
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    _seed_safe_templates(db, company_id, user.id)
    rows = (
        db.query(WorkflowTemplate)
        .filter(WorkflowTemplate.company_id == company_id)
        .order_by(WorkflowTemplate.is_default.desc(), WorkflowTemplate.name.asc())
        .all()
    )
    return [
        {
            "id": t.id,
            "name": t.name,
            "processing_mode": t.processing_mode,
            "is_default": bool(t.is_default),
            "graph_json": t.graph_json,
        }
        for t in rows
    ]


@router.post("/templates", status_code=201)
def create_template(
    body: TemplateCreateRequest,
    user: User = Depends(get_current_user),
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    try:
        validate_graph_structure(body.graph_json)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    mode = (body.processing_mode or "AR").upper()
    if body.is_default:
        db.query(WorkflowTemplate).filter(
            WorkflowTemplate.company_id == company_id,
            WorkflowTemplate.processing_mode == mode,
        ).update({WorkflowTemplate.is_default: False})
    tpl = WorkflowTemplate(
        id=str(uuid.uuid4()),
        company_id=company_id,
        name=body.name[:200],
        processing_mode=(body.processing_mode or "AR").upper(),
        graph_json=body.graph_json,
        is_default=body.is_default,
        created_by=user.id,
    )
    db.add(tpl)
    db.commit()
    return {"id": tpl.id, "name": tpl.name}


@router.patch("/templates/{template_id}")
def patch_template(
    template_id: str,
    body: TemplatePatchRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    tpl = (
        db.query(WorkflowTemplate)
        .filter(
            WorkflowTemplate.id == template_id,
            WorkflowTemplate.company_id == company_id,
        )
        .first()
    )
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    if body.name is not None:
        tpl.name = body.name[:200]
    if body.graph_json is not None:
        try:
            validate_graph_structure(body.graph_json)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        tpl.graph_json = body.graph_json
    if body.is_default is not None:
        if body.is_default:
            db.query(WorkflowTemplate).filter(
                WorkflowTemplate.company_id == company_id,
                WorkflowTemplate.processing_mode == tpl.processing_mode,
                WorkflowTemplate.id != tpl.id,
            ).update({WorkflowTemplate.is_default: False})
        tpl.is_default = body.is_default
    db.commit()
    return {"id": tpl.id, "name": tpl.name, "is_default": bool(tpl.is_default)}


@router.delete("/templates/{template_id}", status_code=204)
def delete_template(
    template_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    tpl = (
        db.query(WorkflowTemplate)
        .filter(
            WorkflowTemplate.id == template_id,
            WorkflowTemplate.company_id == company_id,
        )
        .first()
    )
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(tpl)
    db.commit()


# ── Folders ───────────────────────────────────────────────────────────────


@router.get("/folders")
def list_folders(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(WorkflowFolder)
        .filter(WorkflowFolder.company_id == company_id)
        .order_by(WorkflowFolder.sort_order.asc(), WorkflowFolder.name.asc())
        .all()
    )
    return [
        {
            "id": f.id,
            "name": f.name,
            "parent_id": f.parent_id,
            "sort_order": f.sort_order,
            "mode": f.mode,
        }
        for f in rows
    ]


@router.post("/folders", status_code=201)
def create_folder(
    body: FolderCreateRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    name = (body.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Folder name required")
    folder = WorkflowFolder(
        id=str(uuid.uuid4()),
        company_id=company_id,
        name=name[:200],
        sort_order=0,
        mode=(body.mode or "").upper() or None,
    )
    db.add(folder)
    db.commit()
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id, "mode": folder.mode}


@router.patch("/folders/{folder_id}")
def patch_folder(
    folder_id: str,
    body: FolderPatchRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    folder = (
        db.query(WorkflowFolder)
        .filter(WorkflowFolder.id == folder_id, WorkflowFolder.company_id == company_id)
        .first()
    )
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    if body.name is not None:
        folder.name = body.name.strip()[:200]
    if body.sort_order is not None:
        folder.sort_order = body.sort_order
    db.commit()
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "sort_order": folder.sort_order,
    }


@router.delete("/folders/{folder_id}", status_code=204)
def delete_folder(
    folder_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    folder = (
        db.query(WorkflowFolder)
        .filter(WorkflowFolder.id == folder_id, WorkflowFolder.company_id == company_id)
        .first()
    )
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    db.query(WorkflowRun).filter(WorkflowRun.folder_id == folder.id).update(
        {WorkflowRun.folder_id: None}
    )
    db.delete(folder)
    db.commit()


@router.get("/default-graph/{mode}")
def get_default_graph(mode: str):
    m = (mode or "AR").upper()
    return build_default_graph(m)

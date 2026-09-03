from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.api.ocr import WorkflowRunCancelled, ocr_test_core
from app.api.reconciliation import AccountCodeDeployRequest, DeployTxn, deploy_account_codes
from app.core.db_concurrency import long_running_db_work_slot
from app.graph.branch_routing import filter_run_files_for_node, node_has_branch_input
from app.graph.default_graphs import build_default_graph
from app.graph.graph_schema_v2 import ensure_graph_v2
from app.graph.graph_utils import (
    OCR_PRODUCER_NODE_TYPES,
    double_check_node_params,
    find_node_by_type,
    graph_nodes,
    is_vote_path,
    model_override_for_mode,
    node_data,
    receipt_settings,
    receipt_settings_for_run,
    terminal_ocr_producer_node_id,
    validate_graph_for_execute,
    vlm_node_params,
    vlm_settings,
)
from app.graph.vlm_call_budget import (
    can_make_vlm_call,
    record_vlm_call,
)
from app.graph.workflow_path import (
    assert_graph_unchanged_or_raise,
    store_executed_graph_hash,
)
from app.graph.node_runtime import (
    cache_key_for_vlm,
    find_cached_execution,
    record_node_execution,
)
from app.models.chat import ChatTask, TaskFile, TaskMessage
from app.models.transaction import BankTransaction, LedgerTransaction, TransactionStatus
from app.models.workflow import WorkflowNodeExecution, WorkflowPool2Package, WorkflowRun, WorkflowRunFile
from app.services.pool2_storage import pool2
from app.services.file_storage import storage
from app.services.extraction_validation import (
    _ar_ap_file_key,
    clean_manager_ar_ap_rows,
    dedupe_ar_ap_rows_within_file,
)
from app.services.re_vlm_hints import (
    normalize_expected_receipt_count,
    rescan_reason_labels,
    sanitize_rescan_note,
    validate_rescan_reasons,
)

logger = logging.getLogger(__name__)

_PROCESSABLE_FILE_STATUSES = frozenset({"pending", "warning", "failed"})
_DRAFT_RESET_FILE_STATUSES = frozenset({"running", "ok"})
_CANCEL_REQUESTED_KEY = "cancel_requested"
_VLM_NODE_TYPES = frozenset({"VLM_API", "VLMDoubleCheck", "VLMProposer"})


def _clear_run_cancel(run: WorkflowRun) -> None:
    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    if _CANCEL_REQUESTED_KEY in states:
        states.pop(_CANCEL_REQUESTED_KEY, None)
        run.node_states_json = states


def workflow_run_cancel_requested(run_id: str) -> bool:
    """Fresh read so a cancel POST is visible during long-running VLM."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        row = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if not row:
            return False
        states = row.node_states_json if isinstance(row.node_states_json, dict) else {}
        return bool(states.get(_CANCEL_REQUESTED_KEY))
    finally:
        db.close()


def workflow_run_should_abort_processing(run_id: str | None) -> bool:
    """True when in-flight OCR/VLM should stop (hard cancel or stop flag)."""
    if not run_id:
        return False
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        row = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        if not row:
            return False
        states = row.node_states_json if isinstance(row.node_states_json, dict) else {}
        if states.get(_CANCEL_REQUESTED_KEY):
            return True
        return (row.run_status or "") != "executing"
    finally:
        db.close()


def _active_vlm_node_id(run: WorkflowRun) -> str:
    states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
    graph = run.graph_json if isinstance(run.graph_json, dict) else {}
    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "")
        node_type = str(node.get("type") or "")
        if node_type not in _VLM_NODE_TYPES:
            continue
        entry = states.get(node_id)
        if isinstance(entry, dict) and str(entry.get("status") or "").lower() in _RUNNING_NODE_STATUSES:
            return node_id or "vlm"
    for node in nodes:
        if isinstance(node, dict) and node.get("type") == "VLM_API":
            return str(node.get("id") or "vlm")
    return "vlm"


def _cancel_running_workflow_nodes(run: WorkflowRun, detail: Any = None) -> None:
    primary_id = _active_vlm_node_id(run)
    states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
    for node_id, entry in list(states.items()):
        if node_id == _CANCEL_REQUESTED_KEY or not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "").lower() in _RUNNING_NODE_STATUSES:
            node_detail = detail if node_id == primary_id else None
            _set_node_state(run, node_id, "cancelled", node_detail)


async def _maybe_exit_vlm_loop(
    db: Session,
    run: WorkflowRun,
    *,
    node_id: str,
    finalize_table: bool,
    event_hub: Any,
) -> dict[str, Any] | None:
    if not workflow_run_should_abort_processing(run.id):
        return None
    db.refresh(run)
    if (run.run_status or "") != "executing":
        return {"cancelled": True}
    return await _finish_vlm_after_cancel(
        db,
        run,
        node_id=node_id,
        finalize_table=finalize_table,
        event_hub=event_hub,
    )


async def _finish_vlm_after_cancel(
    db: Session,
    run: WorkflowRun,
    *,
    node_id: str,
    finalize_table: bool,
    event_hub: Any,
) -> dict[str, Any]:
    run_files = (
        db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
    )
    for rf in run_files:
        if rf.file_status == "running":
            rf.file_status = "pending"
            rf.error_text = None

    ok_count = sum(1 for rf in run_files if rf.file_status == "ok")
    warn_count = sum(1 for rf in run_files if rf.file_status == "warning")
    merged = _merge_run_files_ocr(run_files)
    _clear_run_cancel(run)
    cancel_detail = {"ok": ok_count, "warnings": warn_count}
    _cancel_running_workflow_nodes(run, cancel_detail)
    _append_console(
        run,
        "warn",
        f"Stopped by user: {ok_count} ok, {warn_count} warning(s); in-progress files reset.",
    )

    if ok_count or warn_count or merged:
        run.run_status = "awaiting_review"
        _apply_vlm_ocr_states(run, run_files, merged)
        if finalize_table:
            _set_node_state(run, "table", "active", {"row_count": len(merged)})
    else:
        run.run_status = "draft"

    db.commit()
    db.refresh(run)
    await event_hub.snapshot(run.id, run.run_status, run.node_states_json)
    return {
        "cancelled": True,
        "merged_ocr": merged,
        "ok_count": ok_count,
        "warn_count": warn_count,
    }


def _raise_no_files_to_process(run_files: list[WorkflowRunFile]) -> None:
    if not run_files:
        raise HTTPException(
            status_code=400,
            detail="No files attached. Upload files before Run.",
        )
    if all(rf.file_status == "ok" for rf in run_files):
        raise HTTPException(
            status_code=400,
            detail="All files already processed. Use Re-VLM to run OCR again.",
        )
    if all(rf.batch_committed_at is not None for rf in run_files):
        raise HTTPException(
            status_code=400,
            detail="No new files to process. Use Re-VLM to retry failed files.",
        )
    raise HTTPException(status_code=400, detail="No files to process")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _append_console(run: WorkflowRun, level: str, message: str) -> None:
    logs = run.console_log_json if isinstance(run.console_log_json, list) else []
    logs.append({"ts": _now().isoformat(), "level": level, "message": message})
    run.console_log_json = logs[-30:]


_TERMINAL_NODE_STATUSES = {"completed", "done", "ok", "failed", "skipped", "error", "cancelled"}
_RUNNING_NODE_STATUSES = {"running", "executing", "coa_running"}


def _set_node_state(run: WorkflowRun, node_id: str, status: str, detail: Any = None) -> None:
    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    prior = states.get(node_id) if isinstance(states.get(node_id), dict) else {}
    now_iso = datetime.now(timezone.utc).isoformat()
    entry: dict[str, Any] = {"status": status, "detail": detail}
    # Preserve the first start time; stamp it when a node begins running.
    started_at = prior.get("started_at")
    if started_at is None and status == "running":
        started_at = now_iso
    if started_at is not None:
        entry["started_at"] = started_at
    # On a terminal status, record finish time and elapsed duration.
    if status in _TERMINAL_NODE_STATUSES:
        entry["finished_at"] = now_iso
        if started_at is not None:
            try:
                start_dt = datetime.fromisoformat(started_at)
                entry["duration_ms"] = max(
                    0, int((datetime.now(timezone.utc) - start_dt).total_seconds() * 1000)
                )
            except ValueError:
                pass
    states[node_id] = entry
    run.node_states_json = states


def _graph_node_id_set(graph_json: dict[str, Any] | None) -> set[str]:
    ids: set[str] = set()
    for node in graph_nodes(graph_json):
        if not isinstance(node, dict):
            continue
        node_id = str(node.get("id") or "").strip()
        if node_id:
            ids.add(node_id)
    return ids


def _ocr_producer_node_ids(graph_json: dict[str, Any] | None) -> list[str]:
    ids: list[str] = []
    for node in graph_nodes(graph_json):
        if not isinstance(node, dict):
            continue
        if str(node.get("type") or "") not in OCR_PRODUCER_NODE_TYPES:
            continue
        node_id = str(node.get("id") or "").strip()
        if node_id:
            ids.append(node_id)
    return ids


def _resolve_primary_vlm_node_id(graph_json: dict[str, Any] | None) -> str:
    for node_type in ("VLM_API", "VLMProposer", "VLMDoubleCheck"):
        node = find_node_by_type(graph_json, node_type)
        if node and node.get("id"):
            return str(node["id"])
    ocr_ids = _ocr_producer_node_ids(graph_json)
    return ocr_ids[0] if ocr_ids else "vlm"


def _resolve_table_node_id(graph_json: dict[str, Any] | None) -> str:
    node = find_node_by_type(graph_json, "TableReview")
    if node and node.get("id"):
        return str(node["id"])
    return "table"


# After Approve, rows are transferred into Books modules. Re-VLM must not rewrite
# extraction on these runs (avoids conflicting updates with module data).
_POST_APPROVE_RUN_STATUSES = frozenset({"coa_running", "completed", "done", "saved"})

RE_VLM_LOCKED_DETAIL = (
    "Approved and loaded into modules — Re-VLM is disabled to avoid conflicting updates."
)


def _approved_payload_has_rows(payload: dict[str, Any], processing_mode: str | None) -> bool:
    mode = (processing_mode or "").upper()
    if mode == "BANK":
        rows = payload.get("bankTransactions")
        return isinstance(rows, list) and len(rows) > 0
    arap = payload.get("arapTransactions")
    sheet = payload.get("spreadsheetData")
    return (isinstance(arap, list) and len(arap) > 0) or (
        isinstance(sheet, list) and len(sheet) > 0
    )


def _run_has_locked_approved_table(run: WorkflowRun) -> bool:
    status = (run.run_status or "").lower()
    if status not in _POST_APPROVE_RUN_STATUSES:
        return False
    states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
    approved = states.get("approved_payload")
    if not isinstance(approved, dict):
        return False
    return _approved_payload_has_rows(approved, run.processing_mode)


def _prepare_re_vlm_node_states(
    run: WorkflowRun,
    *,
    file_count: int,
    rescan_reasons: list[str] | None = None,
    rescan_note: str | None = None,
) -> tuple[str, str]:
    """Clear stale Finished node badges and mark OCR producer(s) running for Re-VLM."""
    graph = run.graph_json if isinstance(run.graph_json, dict) else {}
    graph_ids = _graph_node_id_set(graph)
    ocr_ids = _ocr_producer_node_ids(graph)
    vlm_node_id = _resolve_primary_vlm_node_id(graph)
    table_node_id = _resolve_table_node_id(graph)

    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    run.node_states_json = {k: v for k, v in states.items() if k not in graph_ids}

    for node_id in sorted(graph_ids):
        if node_id in ocr_ids:
            continue
        _set_node_state(run, node_id, "pending", None)

    labels = rescan_reason_labels(rescan_reasons or [])
    focus = ", ".join(labels) if labels else None
    safe_note = sanitize_rescan_note(rescan_note) or None
    ocr_detail: dict[str, Any] = {"file_count": file_count}
    if focus:
        ocr_detail["rescan_focus"] = focus
    if safe_note:
        ocr_detail["rescan_note"] = safe_note
    if focus:
        ocr_detail["reason"] = f"Re-VLM: {focus}"
    elif safe_note:
        ocr_detail["reason"] = "Re-VLM"

    for ocr_id in ocr_ids:
        _set_node_state(run, ocr_id, "running", ocr_detail)

    return vlm_node_id, table_node_id


def _dict_rows(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


def _rows_from_enhanced(enhanced: Any) -> list[dict[str, Any]]:
    if not isinstance(enhanced, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("tsv_rows", "rows", "transactions"):
        rows.extend(_dict_rows(enhanced.get(key)))
    return rows


def _stamp_page_on_rows(rows: list[dict[str, Any]], page_num: Any) -> list[dict[str, Any]]:
    try:
        page = int(page_num)
    except (TypeError, ValueError):
        return rows
    if page < 1:
        return rows
    for row in rows:
        if row.get("_page") is None:
            row["_page"] = page
    return rows


def rows_from_ocr_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Collect tabular OCR rows from all known result shapes (AR/AP/BANK/OTHER)."""
    rows: list[dict[str, Any]] = []
    rows.extend(_dict_rows(payload.get("tsv_rows")))
    rows.extend(_dict_rows(payload.get("transactions")))
    rows.extend(_rows_from_enhanced(payload.get("ai_enhanced")))

    pages = payload.get("pages")
    if isinstance(pages, list):
        for page in pages:
            if not isinstance(page, dict):
                continue
            page_num = page.get("page")
            page_rows: list[dict[str, Any]] = []
            page_rows.extend(_dict_rows(page.get("rows")))
            page_rows.extend(_rows_from_enhanced(page.get("ai_enhanced")))
            rows.extend(_stamp_page_on_rows(page_rows, page_num))
    return dedupe_ar_ap_rows_within_file(rows)


def _rescan_prior_summary_for_run_file(run_file: WorkflowRunFile) -> str:
    parts: list[str] = []
    if run_file.file_status:
        parts.append(f"status={run_file.file_status}")
    if run_file.gate_result:
        parts.append(f"gate={run_file.gate_result}")
    if run_file.error_text:
        err = str(run_file.error_text).strip()
        if err:
            parts.append(f"error={err[:120]}")
    payload = run_file.result_summary_json
    if isinstance(payload, dict):
        prior_rows = rows_from_ocr_payload(payload)
        parts.append(f"prior_rows={len(prior_rows)}")
    return "; ".join(parts)


# Include in-flight files so Scenario D page snapshots stream into the review table.
_OCR_LIVE_FILE_STATUSES = frozenset({"ok", "running", "warning"})


def _run_file_has_live_ocr(rf: Any) -> bool:
    return str(getattr(rf, "file_status", "") or "") in _OCR_LIVE_FILE_STATUSES


def _merge_run_files_ocr(run_files: list[WorkflowRunFile]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    for rf in run_files:
        payload = rf.result_summary_json
        if not isinstance(payload, dict) or not _run_file_has_live_ocr(rf):
            continue
        merged.extend(rows_from_ocr_payload(payload))
    return merged


def _rows_from_ocr_by_file_state(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    rows: list[dict[str, Any]] = []
    for value in raw.values():
        if isinstance(value, list):
            rows.extend(row for row in value if isinstance(row, dict))
    return rows


def _merged_ocr_rows(run: WorkflowRun, run_files: list[WorkflowRunFile]) -> list[dict[str, Any]]:
    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    stored = states.get("merged_ocr")
    if isinstance(stored, list) and stored:
        return [row for row in stored if isinstance(row, dict)]
    from_state = _rows_from_ocr_by_file_state(states.get("ocr_by_file"))
    if from_state:
        return from_state
    return _merge_run_files_ocr(run_files)


def _promote_run_to_awaiting_review(
    run: WorkflowRun,
    run_files: list[WorkflowRunFile],
    *,
    console_message: str = "Workflow stopped with errors; review table rows and Approve to continue.",
) -> bool:
    merged = _merged_ocr_rows(run, run_files)
    if not merged:
        return False
    run.run_status = "awaiting_review"
    _set_node_state(run, "table", "active", {"row_count": len(merged)})
    _apply_vlm_ocr_states(run, run_files, merged)
    _append_console(run, "warn", console_message)
    return True


def _filename_stems(rf: WorkflowRunFile) -> tuple[str, str]:
    name = (getattr(rf, "original_filename", None) or rf.task_file_id or "").strip().lower()
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return name, stem


def resolve_row_task_file_id(row: dict[str, Any], run_files: list[WorkflowRunFile]) -> str | None:
    file_key = _ar_ap_file_key(row).lower()
    for rf in run_files:
        name, stem = _filename_stems(rf)
        if file_key and file_key == rf.task_file_id.lower():
            return rf.task_file_id
        if file_key and name and (file_key.startswith(stem) or stem in file_key or name in file_key):
            return rf.task_file_id
    if len(run_files) == 1:
        return run_files[0].task_file_id
    return None


def row_file_ids_for_rows(
    rows: list[dict[str, Any]],
    run_files: list[WorkflowRunFile],
) -> list[str]:
    return [resolve_row_task_file_id(row, run_files) or "" for row in rows]


def ocr_by_file_from_merged_rows(
    rows: list[dict[str, Any]],
    run_files: list[WorkflowRunFile],
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        file_id = resolve_row_task_file_id(row, run_files)
        key = file_id or "workflow"
        out.setdefault(key, []).append(row)
    return out


def _ocr_by_file_from_run_files(run_files: list[WorkflowRunFile]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for rf in run_files:
        payload = rf.result_summary_json
        if not isinstance(payload, dict) or not _run_file_has_live_ocr(rf):
            continue
        rows = rows_from_ocr_payload(payload)
        if rows:
            out[rf.task_file_id] = rows
    return out


def apply_partial_ocr_to_running_file(
    run: WorkflowRun,
    run_files: list[WorkflowRunFile],
    running_file: WorkflowRunFile,
    result_json: dict[str, Any],
) -> None:
    """Stamp mid-flight OCR pages onto a still-running file and refresh ocr_by_file."""
    running_file.result_summary_json = result_json
    merged = _merge_run_files_ocr(run_files)
    _apply_vlm_ocr_states(run, run_files, merged)


def persist_workflow_ocr_partial(
    workflow_run_id: str,
    result_json: dict[str, Any],
) -> dict[str, Any] | None:
    """Commit Scenario D page snapshots so GET /runs + WS can fill the table early."""
    if not workflow_run_id or not isinstance(result_json, dict):
        return None
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == workflow_run_id).first()
        if not run or str(run.run_status) not in {"executing", "queued", "running", "coa_running"}:
            return None
        rf = (
            db.query(WorkflowRunFile)
            .filter(
                WorkflowRunFile.run_id == run.id,
                WorkflowRunFile.file_status == "running",
            )
            .first()
        )
        if not rf:
            return None
        live = db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
        apply_partial_ocr_to_running_file(run, live, rf, result_json)
        db.commit()
        return {
            "run_status": run.run_status,
            "node_states_json": run.node_states_json,
        }
    except Exception:
        db.rollback()
        logger.exception(
            "[OCR Partial] failed to persist workflow snapshot for run %s",
            workflow_run_id,
        )
        return None
    finally:
        db.close()


def _apply_vlm_ocr_states(
    run: WorkflowRun,
    run_files: list[WorkflowRunFile],
    merged: list[dict[str, Any]],
) -> None:
    """Update merged OCR state; on merge paths store manager-approved rows per file."""
    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    if states.get("table_source") == "merge" and merged:
        states["merged_ocr"] = merged
        by_merged = ocr_by_file_from_merged_rows(merged, run_files)
        # Multi-file merges without row→file attribution: keep per-file OCR from run files.
        if set(by_merged.keys()) == {"workflow"} and len(run_files) > 1:
            from_files = _ocr_by_file_from_run_files(run_files)
            states["ocr_by_file"] = from_files if from_files else by_merged
        else:
            states["ocr_by_file"] = by_merged
    else:
        ocr_by_file = _ocr_by_file_from_run_files(run_files)
        if ocr_by_file:
            states["ocr_by_file"] = ocr_by_file
        elif merged:
            states["ocr_by_file"] = {"workflow": merged}
        if merged:
            states["merged_ocr"] = merged
    run.node_states_json = states


def _ocr_node_detail_summary(
    merged: list[dict[str, Any]],
    *,
    ok_count: int = 0,
    warn_count: int = 0,
    capped_count: int = 0,
    rescan_focus: str | None = None,
    rescan_note: str | None = None,
) -> dict[str, Any]:
    """Rich node detail for Processing UI (matches vote-path plugin summaries)."""
    from app.graph.nodes.handlers import _feedback_from_rows, _node_detail_summary

    detail = _node_detail_summary(
        row_count=len(merged),
        ok=ok_count,
        warnings=warn_count,
        capped=capped_count,
        feedback=_feedback_from_rows(merged),
    )
    focus = (rescan_focus or "").strip()
    note = (rescan_note or "").strip()
    if focus:
        detail["rescan_focus"] = focus
    if note:
        detail["rescan_note"] = note
    if warn_count > 0 and not detail.get("reason"):
        detail["reason"] = f"{warn_count} file(s) need review"
    elif focus and not detail.get("reason"):
        detail["reason"] = f"Re-VLM: {focus}"
    return detail


def _finalize_run_after_vlm(
    run: WorkflowRun,
    run_files: list[WorkflowRunFile],
    *,
    ok_count: int,
    warn_count: int,
    console_message: str,
    vlm_node_id: str = "vlm",
    table_node_id: str = "table",
    ocr_node_ids: list[str] | None = None,
    merged_rows: list[dict[str, Any]] | None = None,
    rescan_focus: str | None = None,
    rescan_note: str | None = None,
) -> None:
    merged = merged_rows if merged_rows is not None else _merge_run_files_ocr(run_files)
    run.run_status = "awaiting_review"
    completed_ids = ocr_node_ids if ocr_node_ids else [vlm_node_id]
    detail = _ocr_node_detail_summary(
        merged,
        ok_count=ok_count,
        warn_count=warn_count,
        rescan_focus=rescan_focus,
        rescan_note=rescan_note,
    )
    for node_id in completed_ids:
        _set_node_state(run, node_id, "completed", detail)
    table_detail = _ocr_node_detail_summary(
        merged,
        ok_count=ok_count,
        warn_count=warn_count,
        rescan_focus=rescan_focus,
        rescan_note=rescan_note,
    )
    _set_node_state(run, table_node_id, "active", table_detail)
    _append_console(run, "info" if ok_count else "warn", console_message)
    _apply_vlm_ocr_states(run, run_files, merged)


async def _stream_incremental_table(
    db: Session,
    run: WorkflowRun,
    *,
    finalize_table: bool,
    event_hub: Any,
) -> None:
    """Push per-file VLM progress so the review table streams rows during a loop."""
    if workflow_run_should_abort_processing(run.id):
        return
    db.commit()
    db.refresh(run)
    if workflow_run_should_abort_processing(run.id):
        return
    if finalize_table:
        live = db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
        _apply_vlm_ocr_states(run, live, _merge_run_files_ocr(live))
        db.commit()
    await event_hub.snapshot(run.id, run.run_status, run.node_states_json)


class WorkflowService:
    @staticmethod
    def create_run(
        db: Session,
        *,
        company_id: str,
        owner_user_id: str,
        processing_mode: str,
        template_graph: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        mode = (processing_mode or "AR").upper()
        task_id = str(uuid.uuid4())
        run_id = str(uuid.uuid4())
        task = ChatTask(
            id=task_id,
            company_id=company_id,
            owner_user_id=owner_user_id,
            title="Untitled",
            processing_mode=mode,
            status="idle",
        )
        graph = ensure_graph_v2(template_graph if template_graph else build_default_graph(mode), mode)
        run = WorkflowRun(
            id=run_id,
            company_id=company_id,
            task_id=task_id,
            owner_user_id=owner_user_id,
            processing_mode=mode,
            graph_json=graph,
            run_status="draft",
            node_states_json={},
            title="Untitled",
            console_log_json=[],
        )
        db.add(task)
        db.add(run)
        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    def sync_run_files(db: Session, run: WorkflowRun) -> list[WorkflowRunFile]:
        task_files = (
            db.query(TaskFile)
            .filter(TaskFile.task_id == run.task_id, TaskFile.deleted_at.is_(None))
            .all()
        )
        existing = {
            row.task_file_id: row
            for row in db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
        }
        active_task_ids = {tf.id for tf in task_files}
        for task_file_id, row in list(existing.items()):
            if task_file_id not in active_task_ids:
                db.delete(row)
                existing.pop(task_file_id, None)
        out: list[WorkflowRunFile] = []
        for tf in task_files:
            if tf.id in existing:
                out.append(existing[tf.id])
                continue
            row = WorkflowRunFile(
                id=str(uuid.uuid4()),
                run_id=run.id,
                task_file_id=tf.id,
                file_status="pending",
            )
            db.add(row)
            out.append(row)
        db.commit()
        return out

    @staticmethod
    def recover_stuck_execution(db: Session, run: WorkflowRun) -> None:
        """Reset run/files left in executing/running after a failed execute."""
        changed = False
        run_files = (
            db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
        )
        if (run.run_status or "") == "executing":
            if _promote_run_to_awaiting_review(run, run_files):
                changed = True
            else:
                run.run_status = "draft"
                changed = True
        states = dict(run.node_states_json) if isinstance(getattr(run, "node_states_json", None), dict) else {}
        if _CANCEL_REQUESTED_KEY in states:
            _clear_run_cancel(run)
            changed = True
        for rf in run_files:
            if rf.file_status == "running":
                rf.file_status = "pending"
                rf.error_text = None
                changed = True
        if changed:
            db.commit()

    @staticmethod
    def _recover_wedged_node_states(db: Session, run: WorkflowRun) -> None:
        """Clear node states left in a running state by an interrupted execute."""
        states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
        changed = False
        for node_id, entry in list(states.items()):
            if not isinstance(entry, dict):
                continue
            if str(entry.get("status") or "").lower() in _RUNNING_NODE_STATUSES:
                _set_node_state(run, node_id, "cancelled")
                changed = True
        if changed:
            _append_console(run, "info", "Stopped — cleared in-progress steps.")
        WorkflowService.recover_stuck_execution(db, run)
        if changed:
            db.commit()

    @staticmethod
    async def request_run_cancel(db: Session, run: WorkflowRun) -> WorkflowRun:
        """Hard stop: reset run/files/nodes immediately; in-flight OCR aborts on next check."""
        from app.graph.workflow_events import workflow_event_hub

        if (run.run_status or "") != "executing":
            WorkflowService._recover_wedged_node_states(db, run)
            db.refresh(run)
            return run

        states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
        states[_CANCEL_REQUESTED_KEY] = True
        run.node_states_json = states
        db.commit()

        node_id = _active_vlm_node_id(run)
        graph = run.graph_json if isinstance(run.graph_json, dict) else {}
        finalize_table = terminal_ocr_producer_node_id(graph, node_id) and not is_vote_path(graph)
        await _finish_vlm_after_cancel(
            db,
            run,
            node_id=node_id,
            finalize_table=bool(finalize_table),
            event_hub=workflow_event_hub,
        )
        db.refresh(run)
        return run

    @staticmethod
    def move_run_file_to_batch(
        db: Session,
        run: WorkflowRun,
        task_file_id: str,
        upload_batch_id: str,
    ) -> None:
        batch_id = (upload_batch_id or "").strip()
        if not batch_id:
            raise HTTPException(status_code=400, detail="upload_batch_id is required")

        run_file = (
            db.query(WorkflowRunFile)
            .filter(
                WorkflowRunFile.run_id == run.id,
                WorkflowRunFile.task_file_id == task_file_id,
            )
            .first()
        )
        if not run_file:
            raise HTTPException(status_code=404, detail="File not found on run")

        target_peer = (
            db.query(WorkflowRunFile)
            .filter(
                WorkflowRunFile.run_id == run.id,
                WorkflowRunFile.upload_batch_id == batch_id,
                WorkflowRunFile.task_file_id != task_file_id,
            )
            .first()
        )
        run_file.upload_batch_id = batch_id
        if target_peer:
            if target_peer.batch_table_preset:
                run_file.batch_table_preset = target_peer.batch_table_preset
            if target_peer.batch_receipt_signal:
                run_file.batch_receipt_signal = target_peer.batch_receipt_signal
            if target_peer.uploaded_at and not run_file.uploaded_at:
                run_file.uploaded_at = target_peer.uploaded_at
        db.commit()

    @staticmethod
    def _ledger_signature_from_row(row: dict[str, Any], module: str) -> tuple[str, str, datetime, float] | None:
        from app.api.reconciliation import _parse_amount, _parse_date

        voucher = str(row.get("voucher_no") or row.get("id_number") or row.get("doc_id") or "").strip()
        amount = _parse_amount(row.get("amount"))
        book_date = _parse_date(row.get("date"))
        if not voucher or amount is None or book_date is None:
            return None
        return (module.upper(), voucher, book_date, float(amount))

    @staticmethod
    def _bank_signature_from_row(row: dict[str, Any]) -> tuple[datetime, float, str] | None:
        from app.api.reconciliation import _parse_amount, _parse_date

        date_raw = row.get("date") or row.get("transaction_date") or row.get("bank_date")
        bank_date = _parse_date(date_raw)
        deposit = row.get("deposit") if row.get("deposit") not in (None, "", 0) else None
        withdrawal = row.get("withdrawal") if row.get("withdrawal") not in (None, "", 0) else None
        amount = _parse_amount(deposit) if deposit is not None else _parse_amount(withdrawal)
        if bank_date is None or amount is None:
            return None
        currency = str(row.get("currency") or row.get("幣別") or "HKD").strip() or "HKD"
        return (bank_date, float(amount), currency)

    @staticmethod
    def _collect_run_transaction_signatures(
        db: Session,
        run: WorkflowRun,
    ) -> tuple[set[tuple[str, str, datetime, float]], set[tuple[datetime, float, str]]]:
        ledger_sigs: set[tuple[str, str, datetime, float]] = set()
        bank_sigs: set[tuple[datetime, float, str]] = set()
        mode = (run.processing_mode or "").upper()
        states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
        approved = states.get("approved_payload")
        if isinstance(approved, dict):
            if mode in ("AR", "AP"):
                for row in approved.get("arapTransactions") or []:
                    if isinstance(row, dict):
                        sig = WorkflowService._ledger_signature_from_row(row, mode)
                        if sig:
                            ledger_sigs.add(sig)
            elif mode == "BANK":
                for row in approved.get("bankTransactions") or []:
                    if isinstance(row, dict):
                        sig = WorkflowService._bank_signature_from_row(row)
                        if sig:
                            bank_sigs.add(sig)
        run_files = db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
        for rf in run_files:
            payload = rf.result_summary_json
            if not isinstance(payload, dict):
                continue
            if mode in ("AR", "AP"):
                for row in rows_from_ocr_payload(payload):
                    sig = WorkflowService._ledger_signature_from_row(row, mode)
                    if sig:
                        ledger_sigs.add(sig)
            elif mode == "BANK":
                for row in rows_from_ocr_payload(payload):
                    sig = WorkflowService._bank_signature_from_row(row)
                    if sig:
                        bank_sigs.add(sig)
        return ledger_sigs, bank_sigs

    @staticmethod
    def _purge_unreconciled_module_transactions(db: Session, run: WorkflowRun) -> None:
        ledger_sigs, bank_sigs = WorkflowService._collect_run_transaction_signatures(db, run)
        for module, doc_id, book_date, amount in ledger_sigs:
            db.query(LedgerTransaction).filter(
                LedgerTransaction.company_id == run.company_id,
                LedgerTransaction.module == module,
                LedgerTransaction.doc_id == doc_id,
                LedgerTransaction.book_date == book_date,
                LedgerTransaction.amount == amount,
                LedgerTransaction.status == TransactionStatus.UNRECONCILED,
            ).delete(synchronize_session=False)
        for bank_date, amount, currency in bank_sigs:
            db.query(BankTransaction).filter(
                BankTransaction.company_id == run.company_id,
                BankTransaction.bank_date == bank_date,
                BankTransaction.amount == amount,
                BankTransaction.currency == currency,
                BankTransaction.status == TransactionStatus.UNRECONCILED,
            ).delete(synchronize_session=False)

    @staticmethod
    def _purge_run_storage_artifacts(run: WorkflowRun) -> None:
        pool_root = Path(os.getenv("TRANSACTIONS_DIR", "./transactions"))
        for rel in (
            pool_root / run.company_id / "node_outputs" / run.id,
            pool_root / run.company_id / "ar" / run.id,
            pool_root / run.company_id / "ap" / run.id,
            pool_root / run.company_id / "bank" / run.id,
            pool_root / run.company_id / "other" / run.id,
            # Legacy pool path before ASSET_LIA → OTHER rename
            pool_root / run.company_id / "asset_lia" / run.id,
        ):
            if rel.is_dir():
                try:
                    shutil.rmtree(rel, ignore_errors=True)
                except OSError as exc:
                    logger.warning("[WorkflowPurge] Could not remove %s: %s", rel, exc)

    @staticmethod
    def purge_run(db: Session, run: WorkflowRun) -> None:
        """Permanently remove a workflow run, its files, snapshots, and unreconciled module rows."""
        WorkflowService._purge_unreconciled_module_transactions(db, run)

        task_files = (
            db.query(TaskFile)
            .filter(TaskFile.task_id == run.task_id, TaskFile.deleted_at.is_(None))
            .all()
        )
        for task_file in task_files:
            if task_file.storage_path:
                try:
                    Path(task_file.storage_path).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("[WorkflowPurge] Could not delete file from disk: %s", exc)
            task_file.deleted_at = _now()

        db.query(TaskMessage).filter(TaskMessage.task_id == run.task_id).delete(
            synchronize_session=False
        )

        packages = db.query(WorkflowPool2Package).filter(WorkflowPool2Package.run_id == run.id).all()
        for pkg in packages:
            if pkg.storage_path:
                try:
                    Path(pkg.storage_path).unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("[WorkflowPurge] Could not delete pool2 file: %s", exc)

        db.query(WorkflowNodeExecution).filter(WorkflowNodeExecution.run_id == run.id).delete(
            synchronize_session=False
        )
        db.query(WorkflowPool2Package).filter(WorkflowPool2Package.run_id == run.id).delete(
            synchronize_session=False
        )
        db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).delete(
            synchronize_session=False
        )

        WorkflowService._purge_run_storage_artifacts(run)

        task = db.query(ChatTask).filter(ChatTask.id == run.task_id).first()
        if task:
            task.deleted_at = _now()
            task.file_count = 0

        db.delete(run)
        db.commit()

    @staticmethod
    def remove_run_file(
        db: Session,
        run: WorkflowRun,
        task_file_id: str,
    ) -> None:
        run_file = (
            db.query(WorkflowRunFile)
            .filter(
                WorkflowRunFile.run_id == run.id,
                WorkflowRunFile.task_file_id == task_file_id,
            )
            .first()
        )
        if not run_file:
            raise HTTPException(status_code=404, detail="File not found on run")

        task_file = (
            db.query(TaskFile)
            .filter(
                TaskFile.id == task_file_id,
                TaskFile.task_id == run.task_id,
                TaskFile.deleted_at.is_(None),
            )
            .first()
        )
        if not task_file:
            raise HTTPException(status_code=404, detail="File not found")

        if task_file.storage_path:
            try:
                Path(task_file.storage_path).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("[WorkflowFiles] Could not delete file from disk: %s", exc)

        task_file.deleted_at = _now()
        db.delete(run_file)
        task = db.query(ChatTask).filter(ChatTask.id == run.task_id).first()
        if task and (task.file_count or 0) > 0:
            task.file_count = (task.file_count or 0) - 1
        db.commit()

    @staticmethod
    def prepare_run_files_for_execute(
        db: Session,
        run: WorkflowRun,
    ) -> tuple[list[WorkflowRunFile], list[WorkflowRunFile]]:
        """Sync task files, recover draft-run state, return (processable, all_run_files)."""
        run_files = WorkflowService.sync_run_files(db, run)
        if (run.run_status or "draft") == "draft":
            changed = False
            for rf in run_files:
                if rf.file_status not in _DRAFT_RESET_FILE_STATUSES:
                    continue
                # Keep committed batch files on Option D multi-batch runs.
                if rf.file_status == "ok" and rf.batch_committed_at is not None:
                    continue
                rf.file_status = "pending"
                rf.error_text = None
                rf.gate_result = None
                changed = True
            if changed:
                db.commit()
        processable = [
            rf for rf in run_files
            if rf.file_status in _PROCESSABLE_FILE_STATUSES
            and rf.batch_committed_at is None
        ]
        if not processable:
            # Un-wedge a run whose files were all committed by a prior crashed or
            # interrupted execute: reopen the non-ok committed files so Run works
            # again instead of failing file prep forever. Multi-batch runs with new
            # uncommitted files never reach here, so committed failures still skip.
            unwedged = False
            for rf in run_files:
                if rf.file_status != "ok" and rf.batch_committed_at is not None:
                    rf.batch_committed_at = None
                    unwedged = True
            if unwedged:
                db.commit()
                processable = [
                    rf for rf in run_files
                    if rf.file_status in _PROCESSABLE_FILE_STATUSES
                    and rf.batch_committed_at is None
                ]
        if not processable:
            _raise_no_files_to_process(run_files)
        now = _now()
        fallback_batch_id = str(uuid.uuid4())
        states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
        receipt_signal, table_preset = receipt_settings_for_run(run.graph_json, states)
        frozen_preset = table_preset or "default"
        for rf in processable:
            rf.batch_committed_at = now
            if not rf.upload_batch_id:
                rf.upload_batch_id = fallback_batch_id
            if not rf.uploaded_at:
                rf.uploaded_at = now
            rf.batch_table_preset = frozen_preset
            rf.batch_receipt_signal = receipt_signal
        db.commit()
        return processable, run_files

    @staticmethod
    async def record_files_node_output(
        db: Session,
        run: WorkflowRun,
        node_id: str,
        run_files: list[WorkflowRunFile],
    ) -> None:
        items = [
            {
                "file_id": rf.task_file_id,
                "run_file_id": rf.id,
                "file_status": rf.file_status,
            }
            for rf in run_files
        ]
        record_node_execution(
            db,
            run,
            node_id=node_id,
            node_type="Files",
            status="completed",
            payload={"files": items},
        )

    @staticmethod
    def normalize_run_graph(run: WorkflowRun) -> None:
        run.graph_json = ensure_graph_v2(run.graph_json, run.processing_mode)

    @staticmethod
    def _vlm_kwargs_for_node(
        run: WorkflowRun,
        node: dict[str, Any] | None,
        *,
        force_cross: bool = False,
    ) -> dict[str, Any]:
        params = vlm_node_params(run.graph_json)
        if node:
            data = node_data(node)
            params = {
                "provider": str(data.get("provider") or params["provider"]),
                "model": data.get("model"),
                "crossVlm": bool(data.get("crossVlm")),
                "promptPreset": str(data.get("promptPreset") or "default"),
            }
        mode = (run.processing_mode or "").upper()
        provider = str(params.get("provider") or "Qwen")
        model = model_override_for_mode(mode, provider, params.get("model"))
        cross = force_cross or bool(params.get("crossVlm"))
        kwargs: dict[str, Any] = {"ap_force_cross_verify": cross if mode == "AP" else False}
        if model:
            kwargs["ap_vlm_model_override"] = model
        return kwargs

    @staticmethod
    async def _process_one_file(
        db: Session,
        run: WorkflowRun,
        run_file: WorkflowRunFile,
        task_file: TaskFile,
        *,
        force_process: bool = False,
        receipt_signal: str | None = None,
        table_preset: str | None = None,
        multi_receipt_confirmed: bool = False,
        ap_vlm_model_override: str | None = None,
        ap_force_cross_verify: bool = False,
        rescan_reasons: list[str] | None = None,
        rescan_note: str | None = None,
        expected_receipt_count: int | None = None,
    ) -> dict[str, Any]:
        from app.services.abuse_guard import company_ocr_concurrency

        if workflow_run_should_abort_processing(run.id):
            return {"ok": False, "cancelled": True}

        run_file.file_status = "running"
        db.commit()

        path = task_file.storage_path
        if not path or not os.path.isfile(path):
            run_file.file_status = "failed"
            run_file.error_text = "File missing on disk"
            db.commit()
            return {"ok": False, "error": run_file.error_text}

        filename = task_file.original_filename or "upload"
        with open(path, "rb") as handle:
            content = handle.read()
        upload = UploadFile(filename=filename, file=BytesIO(content))
        trace_id = str(uuid.uuid4())
        validated_reasons = validate_rescan_reasons(rescan_reasons)
        safe_note = sanitize_rescan_note(rescan_note)
        expected_count = normalize_expected_receipt_count(expected_receipt_count)
        prior_summary = _rescan_prior_summary_for_run_file(run_file)

        try:
            async with company_ocr_concurrency(run.company_id):
                async with long_running_db_work_slot():
                    result = await ocr_test_core(
                        file=upload,
                        processing_mode=run.processing_mode,
                        multi_receipt_confirmed=multi_receipt_confirmed,
                        multi_receipt_acknowledged=False,
                        force_process=force_process,
                        company_id=run.company_id,
                        trace_id=trace_id,
                        db=db,
                        ap_vlm_receipt_signal=receipt_signal,
                        ap_vlm_table_preset=table_preset,
                        ap_vlm_model_override=ap_vlm_model_override,
                        ap_force_cross_verify=ap_force_cross_verify,
                        workflow_run_id=run.id,
                        rescan_reasons=validated_reasons or None,
                        rescan_note=safe_note or None,
                        rescan_prior_summary=prior_summary or None,
                        expected_receipt_count=expected_count,
                    )
        except WorkflowRunCancelled:
            run_file.file_status = "pending"
            run_file.error_text = None
            db.commit()
            return {"ok": False, "cancelled": True}
        except HTTPException as he:
            detail = he.detail if isinstance(he.detail, str) else str(he.detail)
            run_file.file_status = "failed"
            run_file.error_text = detail[:2000]
            run_file.result_summary_json = {"http_status": he.status_code}
            db.commit()
            return {"ok": False, "error": detail}
        except Exception as exc:
            logger.exception("VLM failed for run_file %s", run_file.id)
            run_file.file_status = "failed"
            run_file.error_text = str(exc)[:2000]
            db.commit()
            return {"ok": False, "error": run_file.error_text}

        if workflow_run_should_abort_processing(run.id):
            run_file.file_status = "pending"
            run_file.error_text = None
            db.commit()
            return {"ok": False, "cancelled": True}

        gate = result.get("gate_result")
        if gate and str(gate) != "TRANSACTIONAL" and not force_process:
            run_file.file_status = "warning"
            run_file.gate_result = str(gate)
            run_file.error_text = str(result.get("gate_message") or gate)
            run_file.result_summary_json = result
            db.commit()
            return {"ok": False, "warning": True, "gate_result": gate, "result": result}

        if result.get("needs_confirmation") and not multi_receipt_confirmed:
            run_file.file_status = "warning"
            run_file.error_text = "needs_confirmation"
            run_file.result_summary_json = result
            db.commit()
            return {"ok": False, "warning": True, "needs_confirmation": True, "result": result}

        count_val = result.get("count_validation") if isinstance(result, dict) else None
        if isinstance(count_val, dict):
            if (
                count_val.get("status") == "mismatch"
                and str(count_val.get("assertion_strength") or "").lower() == "hard"
            ):
                expected = count_val.get("expected_receipt_count")
                accepted = count_val.get("accepted_region_count")
                extracted = count_val.get("extracted_region_count")
                run_file.file_status = "warning"
                run_file.error_text = (
                    f"count_mismatch: expected={expected} "
                    f"accepted={accepted} extracted={extracted}"
                )
                run_file.result_summary_json = result
                db.commit()
                return {
                    "ok": False,
                    "warning": True,
                    "count_mismatch": True,
                    "result": result,
                }

        run_file.file_status = "ok"
        run_file.gate_result = None
        run_file.error_text = None
        run_file.result_summary_json = result
        db.commit()
        return {"ok": True, "result": result}

    @staticmethod
    def _vlm_ocr_kwargs(run: WorkflowRun) -> dict[str, Any]:
        return WorkflowService._vlm_kwargs_for_node(run, None)

    @staticmethod
    async def execute_vlm_stage(
        db: Session,
        run: WorkflowRun,
        *,
        node: dict[str, Any] | None = None,
        emit: Any = None,
        finalize_table: bool | None = None,
        allow_reprocess: bool = False,
    ) -> dict[str, Any]:
        from app.graph.workflow_events import workflow_event_hub

        node_id = str((node or {}).get("id") or "vlm")
        node_type = str((node or {}).get("type") or "VLM_API")
        states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
        receipt_signal, table_preset = receipt_settings_for_run(run.graph_json, states)
        multi_confirmed = receipt_signal == "multi_per_page"
        vlm_kwargs = WorkflowService._vlm_kwargs_for_node(run, node)
        provider = str(
            node_data(node).get("provider")
            if node
            else vlm_node_params(run.graph_json).get("provider") or "Qwen"
        )

        if finalize_table is None:
            finalize_table = terminal_ocr_producer_node_id(run.graph_json, node_id) and not is_vote_path(
                run.graph_json
            )

        run_files = (
            db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
        )
        if allow_reprocess:
            targets = [rf for rf in run_files if rf.file_status in ("ok", "warning", "pending", "failed")]
        else:
            pending, run_files = WorkflowService.prepare_run_files_for_execute(db, run)
            targets = list(pending)

        if node and node_has_branch_input(run.graph_json, node_id):
            branch_files = filter_run_files_for_node(run, node, run_files)
            branch_ids = {rf.task_file_id for rf in branch_files}
            targets = [rf for rf in targets if rf.task_file_id in branch_ids]

        if not targets:
            raise HTTPException(
                status_code=400,
                detail="No files routed to VLM for processing. Check workflow branches or use Re-VLM on pending files.",
            )

        _clear_run_cancel(run)

        run.run_status = "executing"
        _set_node_state(run, node_id, "running", {"file_count": len(targets)})
        _append_console(run, "info", f"Running VLM on {len(targets)} file(s)...")
        db.commit()
        await workflow_event_hub.snapshot(run.id, run.run_status, run.node_states_json)

        task_files = {
            tf.id: tf
            for tf in db.query(TaskFile)
            .filter(TaskFile.task_id == run.task_id, TaskFile.deleted_at.is_(None))
            .all()
        }

        proposal_snapshots: list[dict[str, Any]] = []
        capped_files: list[str] = []

        async def _one(rf: WorkflowRunFile) -> dict[str, Any]:
            if not can_make_vlm_call(run, node, rf.task_file_id):
                capped_files.append(rf.task_file_id)
                return {"ok": False, "capped": True}
            tf = task_files.get(rf.task_file_id)
            if not tf:
                rf.file_status = "failed"
                rf.error_text = "Task file missing"
                db.commit()
                return {"ok": False}
            file_signal = rf.batch_receipt_signal if rf.batch_receipt_signal else receipt_signal
            file_preset = rf.batch_table_preset if rf.batch_table_preset else table_preset
            ck = cache_key_for_vlm(
                file_id=rf.task_file_id,
                mode=run.processing_mode,
                provider=provider,
                model=vlm_kwargs.get("ap_vlm_model_override"),
                receipt_signal=file_signal,
                table_preset=file_preset,
                force_cross=bool(vlm_kwargs.get("ap_force_cross_verify")),
                node_type=node_type,
            )
            cached = find_cached_execution(db, run.id, node_id, ck)
            if cached and cached.storage_path:
                payload = pool2.load_node_output(cached.storage_path)
                if isinstance(payload, dict) and payload.get("result_summary_json"):
                    rf.result_summary_json = payload["result_summary_json"]
                    rf.file_status = str(payload.get("file_status") or "ok")
                    db.commit()
                    record_vlm_call(run, rf.task_file_id)
                    return {"ok": rf.file_status == "ok", "cached": True}
            file_multi = (file_signal or "") == "multi_per_page"
            prior_payload = rf.result_summary_json if allow_reprocess else None
            result = await WorkflowService._process_one_file(
                db,
                run,
                rf,
                tf,
                force_process=allow_reprocess,
                receipt_signal=file_signal,
                table_preset=file_preset,
                multi_receipt_confirmed=file_multi or multi_confirmed,
                **vlm_kwargs,
            )
            if result.get("ok") or result.get("cached"):
                record_vlm_call(run, rf.task_file_id)
            if allow_reprocess and isinstance(rf.result_summary_json, dict):
                proposal_snapshots.append(
                    {
                        "task_file_id": rf.task_file_id,
                        "result_summary_json": rf.result_summary_json,
                        "prior_result_summary_json": prior_payload,
                    }
                )
            return result

        results: list[dict[str, Any]] = []
        for rf in targets:
            stopped = await _maybe_exit_vlm_loop(
                db,
                run,
                node_id=node_id,
                finalize_table=bool(finalize_table),
                event_hub=workflow_event_hub,
            )
            if stopped is not None:
                return stopped
            results.append(await _one(rf))
            await _stream_incremental_table(
                db, run, finalize_table=bool(finalize_table), event_hub=workflow_event_hub
            )
            stopped = await _maybe_exit_vlm_loop(
                db,
                run,
                node_id=node_id,
                finalize_table=bool(finalize_table),
                event_hub=workflow_event_hub,
            )
            if stopped is not None:
                return stopped

        ok_count = sum(1 for r in results if r.get("ok"))
        warn_count = sum(1 for r in results if r.get("warning"))
        capped_count = sum(1 for r in results if r.get("capped"))

        if allow_reprocess:
            merged: list[dict[str, Any]] = []
            for snap in proposal_snapshots:
                payload = snap.get("result_summary_json")
                if isinstance(payload, dict):
                    merged.extend(rows_from_ocr_payload(payload))
        else:
            run_files = (
                db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
            )
            merged = _merge_run_files_ocr(run_files)
            still_pending = sum(1 for rf in run_files if rf.file_status == "pending")
            if ok_count == 0 and warn_count == 0 and capped_count == 0 and still_pending > 0:
                run.run_status = "draft"
                _set_node_state(run, node_id, "failed", {"pending": still_pending})
                _append_console(
                    run,
                    "error",
                    f"VLM produced no results; {still_pending} file(s) still pending. Check backend logs and OCR API keys.",
                )
                db.commit()
                raise HTTPException(
                    status_code=502,
                    detail="VLM produced no results. Check backend logs and OCR configuration.",
                )

        ocr_payload = {
            "files": proposal_snapshots if allow_reprocess else [
                {
                    "task_file_id": rf.task_file_id,
                    "file_status": rf.file_status,
                    "result_summary_json": rf.result_summary_json,
                }
                for rf in (
                    db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
                )
            ],
            "merged_ocr": merged,
            "capped_files": capped_files,
        }
        if not allow_reprocess:
            record_node_execution(
                db,
                run,
                node_id=node_id,
                node_type=node_type,
                status="completed",
                payload=ocr_payload,
                provider=provider,
                model=vlm_kwargs.get("ap_vlm_model_override"),
            )

        summary = {
            "merged_ocr": merged,
            "ok_count": ok_count,
            "warn_count": warn_count,
            "capped_count": capped_count,
            "proposal_snapshots": proposal_snapshots,
        }

        if finalize_table:
            run_files = (
                db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
            )
            _finalize_run_after_vlm(
                run,
                run_files,
                ok_count=ok_count,
                warn_count=warn_count,
                console_message=f"VLM finished: {ok_count} ok, {warn_count} warning(s).",
                vlm_node_id=node_id,
            )
            store_executed_graph_hash(run)
        else:
            merged_for_detail = merged if isinstance(merged, list) else []
            _set_node_state(
                run,
                node_id,
                "completed",
                _ocr_node_detail_summary(
                    merged_for_detail,
                    ok_count=ok_count,
                    warn_count=warn_count,
                    capped_count=capped_count,
                ),
            )
            _append_console(
                run,
                "info",
                f"VLM node {node_id} finished: {ok_count} ok, {warn_count} warning(s).",
            )

        db.commit()
        db.refresh(run)
        await workflow_event_hub.snapshot(run.id, run.run_status, run.node_states_json)
        return summary

    @staticmethod
    async def execute_double_check_stage(
        db: Session,
        run: WorkflowRun,
        *,
        node: dict[str, Any] | None = None,
        emit: Any = None,
        finalize_table: bool | None = None,
    ) -> dict[str, Any]:
        from app.graph.workflow_events import workflow_event_hub

        node_id = str((node or {}).get("id") or "vlm_double_check")
        dc_params = double_check_node_params(run.graph_json) or {}
        if dc_params.get("enabled") is False:
            _set_node_state(run, node_id, "skipped", None)
            db.commit()
            return {"skipped": True}

        data = node_data(node) if node else {}
        from app.core.config import default_workflow_provider

        provider = str(
            data.get("provider") or dc_params.get("provider") or default_workflow_provider()
        )
        mode = (run.processing_mode or "").upper()
        model = model_override_for_mode(mode, provider, data.get("model") or dc_params.get("model"))
        vlm_kwargs: dict[str, Any] = {"ap_force_cross_verify": mode == "AP"}
        if model:
            vlm_kwargs["ap_vlm_model_override"] = model

        states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
        receipt_signal, table_preset = receipt_settings_for_run(run.graph_json, states)
        multi_confirmed = receipt_signal == "multi_per_page"

        if finalize_table is None:
            finalize_table = terminal_ocr_producer_node_id(run.graph_json, node_id) and not is_vote_path(
                run.graph_json
            )

        all_run_files = (
            db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
        )
        run_files = list(all_run_files)
        if node and node_has_branch_input(run.graph_json, node_id):
            branch_files = filter_run_files_for_node(run, node, run_files)
            if not branch_files:
                _set_node_state(run, node_id, "skipped", {"reason": "no_files_on_branch"})
                _append_console(run, "info", "Double-check skipped: no files on branch.")
                db.commit()
                await workflow_event_hub.snapshot(run.id, run.run_status, run.node_states_json)
                return {"skipped": True, "reason": "no_files_on_branch"}
            branch_ids = {rf.task_file_id for rf in branch_files}
            run_files = [rf for rf in run_files if rf.task_file_id in branch_ids]
        targets = [rf for rf in run_files if rf.file_status in ("ok", "warning")]

        if not targets:
            _set_node_state(run, node_id, "skipped", {"reason": "no_files_on_branch"})
            _append_console(run, "info", "Double-check skipped: no eligible files on branch.")
            db.commit()
            await workflow_event_hub.snapshot(run.id, run.run_status, run.node_states_json)
            return {"skipped": True, "reason": "no_files_on_branch"}

        run.run_status = "executing"
        _set_node_state(run, node_id, "running", {"file_count": len(targets)})
        _append_console(run, "info", f"Double-check VLM on {len(targets)} file(s)...")
        db.commit()

        task_files = {
            tf.id: tf
            for tf in db.query(TaskFile)
            .filter(TaskFile.task_id == run.task_id, TaskFile.deleted_at.is_(None))
            .all()
        }

        capped_files: list[str] = []
        ok_count = 0
        for rf in targets:
            stopped = await _maybe_exit_vlm_loop(
                db,
                run,
                node_id=node_id,
                finalize_table=bool(finalize_table),
                event_hub=workflow_event_hub,
            )
            if stopped is not None:
                return stopped
            if not can_make_vlm_call(run, node, rf.task_file_id):
                capped_files.append(rf.task_file_id)
                continue
            tf = task_files.get(rf.task_file_id)
            if not tf:
                continue
            file_signal = rf.batch_receipt_signal if rf.batch_receipt_signal else receipt_signal
            file_preset = rf.batch_table_preset if rf.batch_table_preset else table_preset
            file_multi = (file_signal or "") == "multi_per_page"
            result = await WorkflowService._process_one_file(
                db,
                run,
                rf,
                tf,
                force_process=True,
                receipt_signal=file_signal,
                table_preset=file_preset,
                multi_receipt_confirmed=file_multi or multi_confirmed,
                **vlm_kwargs,
            )
            if result.get("ok"):
                record_vlm_call(run, rf.task_file_id)
                ok_count += 1
            await _stream_incremental_table(
                db, run, finalize_table=bool(finalize_table), event_hub=workflow_event_hub
            )
            stopped = await _maybe_exit_vlm_loop(
                db,
                run,
                node_id=node_id,
                finalize_table=bool(finalize_table),
                event_hub=workflow_event_hub,
            )
            if stopped is not None:
                return stopped

        merged = _merge_run_files_ocr(all_run_files)
        warn_count = sum(1 for rf in all_run_files if rf.file_status == "warning")
        record_node_execution(
            db,
            run,
            node_id=node_id,
            node_type="VLMDoubleCheck",
            status="completed",
            payload={"merged_ocr": merged, "files": len(targets), "capped_files": capped_files},
            provider=provider,
            model=model,
        )

        if finalize_table:
            _apply_vlm_ocr_states(run, all_run_files, merged)
            _finalize_run_after_vlm(
                run,
                all_run_files,
                ok_count=ok_count,
                warn_count=warn_count,
                console_message=f"Double-check finished. {len(merged)} row(s) in table.",
                vlm_node_id=node_id,
            )
            store_executed_graph_hash(run)
        else:
            _set_node_state(
                run,
                node_id,
                "completed",
                _ocr_node_detail_summary(
                    merged,
                    ok_count=ok_count,
                    warn_count=warn_count,
                    capped_count=len(capped_files),
                ),
            )
            _append_console(run, "info", f"Double-check finished. {len(merged)} row(s).")

        db.commit()
        await workflow_event_hub.snapshot(run.id, run.run_status, run.node_states_json)
        return {"merged_ocr": merged, "ok_count": ok_count, "capped_files": capped_files}

    @staticmethod
    async def execute_run(db: Session, run: WorkflowRun) -> WorkflowRun:
        WorkflowService.normalize_run_graph(run)
        assert_graph_unchanged_or_raise(run)
        db.commit()
        from app.graph.executor import run_workflow_graph
        from app.graph.nodes.registry import NODE_CLASS_MAPPINGS
        from app.graph.workflow_events import workflow_event_hub

        async def _emit(event_type: str, payload: dict[str, Any]) -> None:
            payload = {**payload, "type": event_type}
            await workflow_event_hub.emit(run.id, payload)

        await run_workflow_graph(
            db, run, NODE_CLASS_MAPPINGS, emit=_emit, stop_after="table"
        )
        db.refresh(run)
        return run

    @staticmethod
    async def rerun_workflow(db: Session, run: WorkflowRun) -> WorkflowRun:
        """Full re-run: reset all files and node states, then execute the whole graph.

        Used by Re-VLM so every node card refreshes and downstream nodes (Manager,
        Merge, Vote, etc.) recompute against the new extraction / changed workflow.
        """
        run_files = (
            db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
        )
        for rf in run_files:
            rf.file_status = "pending"
            rf.error_text = None
            rf.gate_result = None
            rf.batch_committed_at = None
        # Clear prior node states (statuses, timing, executed-graph fingerprint) so the
        # canvas resets and the execute guard does not block this deliberate re-run.
        run.node_states_json = {}
        run.run_status = "draft"
        db.commit()
        return await WorkflowService.execute_run(db, run)

    @staticmethod
    async def re_vlm_files(
        db: Session,
        run: WorkflowRun,
        task_file_ids: list[str],
        *,
        force_process: bool = False,
        rescan_reasons: list[str] | None = None,
        rescan_note: str | None = None,
        expected_receipt_count: int | None = None,
    ) -> WorkflowRun:
        if _run_has_locked_approved_table(run):
            raise HTTPException(status_code=409, detail=RE_VLM_LOCKED_DETAIL)
        validated_reasons = validate_rescan_reasons(rescan_reasons)
        safe_note = sanitize_rescan_note(rescan_note)
        expected_count = normalize_expected_receipt_count(expected_receipt_count)
        receipt_signal, table_preset = receipt_settings(run.graph_json)
        multi_confirmed = receipt_signal == "multi_per_page" or force_process
        vlm_kwargs = WorkflowService._vlm_ocr_kwargs(run)
        run_files = (
            db.query(WorkflowRunFile)
            .filter(
                WorkflowRunFile.run_id == run.id,
                WorkflowRunFile.task_file_id.in_(task_file_ids),
            )
            .all()
        )
        if not run_files:
            raise HTTPException(status_code=404, detail="No matching run files")

        frozen_preset = table_preset or "default"
        for rf in run_files:
            rf.batch_table_preset = frozen_preset
            rf.batch_receipt_signal = receipt_signal

        task_files = {
            tf.id: tf
            for tf in db.query(TaskFile)
            .filter(TaskFile.task_id == run.task_id, TaskFile.deleted_at.is_(None))
            .all()
        }

        from app.graph.workflow_events import workflow_event_hub

        graph = run.graph_json if isinstance(run.graph_json, dict) else {}
        ocr_node_ids = _ocr_producer_node_ids(graph)
        vlm_node_id, table_node_id = _prepare_re_vlm_node_states(
            run,
            file_count=len(run_files),
            rescan_reasons=validated_reasons,
            rescan_note=safe_note,
        )
        run.run_status = "executing"
        _append_console(run, "info", f"Re-VLM {len(run_files)} file(s)...")
        if validated_reasons or safe_note or expected_count is not None:
            labels = rescan_reason_labels(validated_reasons)
            reason_text = ", ".join(labels) if labels else "none"
            note_suffix = f"; note: {safe_note}" if safe_note else ""
            count_suffix = (
                f"; expected_receipts: {expected_count}" if expected_count is not None else ""
            )
            _append_console(
                run, "info", f"Re-VLM reasons: {reason_text}{note_suffix}{count_suffix}"
            )
        _clear_run_cancel(run)
        db.commit()
        await workflow_event_hub.snapshot(run.id, run.run_status, run.node_states_json)

        results: list[dict[str, Any]] = []
        for rf in run_files:
            stopped = await _maybe_exit_vlm_loop(
                db,
                run,
                node_id=vlm_node_id,
                finalize_table=True,
                event_hub=workflow_event_hub,
            )
            if stopped is not None:
                db.refresh(run)
                return run
            tf = task_files.get(rf.task_file_id)
            if tf:
                file_signal = rf.batch_receipt_signal if rf.batch_receipt_signal else receipt_signal
                file_preset = rf.batch_table_preset if rf.batch_table_preset else table_preset
                file_multi = (file_signal or "") == "multi_per_page"
                results.append(
                    await WorkflowService._process_one_file(
                        db,
                        run,
                        rf,
                        tf,
                        force_process=force_process,
                        receipt_signal=file_signal,
                        table_preset=file_preset,
                        multi_receipt_confirmed=file_multi or multi_confirmed,
                        rescan_reasons=validated_reasons,
                        rescan_note=safe_note,
                        expected_receipt_count=expected_count,
                        **vlm_kwargs,
                    )
                )
                await _stream_incremental_table(
                    db, run, finalize_table=True, event_hub=workflow_event_hub
                )
                stopped = await _maybe_exit_vlm_loop(
                    db,
                    run,
                    node_id=vlm_node_id,
                    finalize_table=True,
                    event_hub=workflow_event_hub,
                )
                if stopped is not None:
                    db.refresh(run)
                    return run
            else:
                results.append({"ok": False})

        all_run_files = (
            db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
        )
        ok_count = sum(1 for rf in all_run_files if rf.file_status == "ok")
        warn_count = sum(1 for rf in all_run_files if rf.file_status == "warning")
        re_ok = sum(1 for r in results if r.get("ok"))
        re_warn = sum(1 for r in results if r.get("warning"))
        merged = _merge_run_files_ocr(all_run_files)

        rescan_focus: str | None = None
        rescan_note_persist: str | None = None
        states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
        for ocr_id in ocr_node_ids:
            st = states.get(ocr_id)
            if not isinstance(st, dict):
                continue
            d = st.get("detail")
            if not isinstance(d, dict):
                continue
            if not rescan_focus and d.get("rescan_focus"):
                rescan_focus = str(d["rescan_focus"]).strip() or None
            if not rescan_note_persist and d.get("rescan_note"):
                rescan_note_persist = str(d["rescan_note"]).strip() or None
            if rescan_focus or rescan_note_persist:
                break

        _finalize_run_after_vlm(
            run,
            all_run_files,
            ok_count=ok_count,
            warn_count=warn_count,
            console_message=f"Re-VLM finished: {re_ok} ok, {re_warn} warning(s). {len(merged)} row(s) in table.",
            vlm_node_id=vlm_node_id,
            table_node_id=table_node_id,
            ocr_node_ids=ocr_node_ids or None,
            rescan_focus=rescan_focus,
            rescan_note=rescan_note_persist,
        )
        db.commit()
        db.refresh(run)
        return run

    @staticmethod
    async def deploy_approved_coa(
        db: Session,
        run: WorkflowRun,
        approved_payload: dict[str, Any],
        *,
        skip_coa: bool = False,
    ) -> None:
        if skip_coa:
            _set_node_state(run, "coa", "skipped", None)
            db.commit()
            return

        run.run_status = "coa_running"
        _set_node_state(run, "coa", "running", None)
        _append_console(run, "info", "CoA deploy started...")
        db.commit()
        raw_txns = approved_payload.get("arapTransactions") or approved_payload.get("bankTransactions") or []
        if isinstance(raw_txns, list) and raw_txns:
            mode = (run.processing_mode or "AR").upper()
            deploy_txns = [
                DeployTxn.model_validate(t)
                for t in raw_txns
                if isinstance(t, dict)
            ]
            deploy_req = AccountCodeDeployRequest(mode=mode, transactions=deploy_txns)
            try:
                deploy_out = await deploy_account_codes(
                    deploy_req, company_id=run.company_id, db=db
                )
                results = deploy_out.get("results") if isinstance(deploy_out, dict) else []
                by_id = {
                    str(r.get("id_number")): r.get("suggested_code")
                    for r in results
                    if isinstance(r, dict)
                }
                for row in raw_txns:
                    if isinstance(row, dict):
                        idn = str(row.get("id_number") or "")
                        code = by_id.get(idn)
                        if code:
                            row["category"] = code
                _append_console(run, "info", "CoA deploy completed.")
            except HTTPException as he:
                _append_console(run, "error", f"CoA deploy failed: {he.detail}")
            except Exception as exc:
                _append_console(run, "error", f"CoA deploy failed: {exc}")

        _set_node_state(run, "coa", "completed", None)
        db.commit()

    @staticmethod
    async def save_approved_snapshot(
        db: Session,
        run: WorkflowRun,
        approved_payload: dict[str, Any],
        *,
        node: dict[str, Any] | None = None,
    ) -> None:
        task = db.query(ChatTask).filter(ChatTask.id == run.task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        save_node_id = str((node or {}).get("id") or "save")
        table_node_id = "table"

        manifest = {
            "run_id": run.id,
            "task_id": run.task_id,
            "company_id": run.company_id,
            "processing_mode": run.processing_mode,
            "approved_payload": approved_payload,
            "lineage": {
                "graph_version": (run.graph_json or {}).get("schemaVersion"),
            },
        }
        package_id, storage_path = pool2.save_final_package(
            run.company_id,
            run.processing_mode,
            run.id,
            manifest,
        )
        pkg_row = WorkflowPool2Package(
            id=str(uuid.uuid4()),
            run_id=run.id,
            company_id=run.company_id,
            task_id=run.task_id,
            processing_mode=run.processing_mode,
            package_id=package_id,
            storage_path=storage_path,
            manifest_json={"package_id": package_id},
        )
        db.add(pkg_row)

        record_node_execution(
            db,
            run,
            node_id=save_node_id,
            node_type="SaveResult",
            status="completed",
            payload=manifest,
        )
        record_node_execution(
            db,
            run,
            node_id=table_node_id,
            node_type="TableReview",
            status="completed",
            payload=approved_payload,
        )

        db.query(TaskMessage).filter(
            TaskMessage.task_id == run.task_id,
            TaskMessage.content_type == "ocr_snapshot",
            ~TaskMessage.id.like("ocr-batch-%"),
        ).delete(synchronize_session=False)
        last = (
            db.query(TaskMessage)
            .filter(TaskMessage.task_id == run.task_id)
            .order_by(TaskMessage.sequence_index.desc())
            .first()
        )
        next_seq = (last.sequence_index + 1) if last else 0
        msg = TaskMessage(
            id=str(uuid.uuid4()),
            task_id=run.task_id,
            sequence_index=next_seq,
            role="assistant",
            content_text="OCR snapshot",
            content_type="ocr_snapshot",
            payload_json=approved_payload,
        )
        db.add(msg)
        run.snapshot_message_id = msg.id
        pkg_row.snapshot_message_id = msg.id
        task.has_spreadsheet = True
        task.status = "completed"
        task.updated_at = _now()
        run.run_status = "completed"
        states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
        states["pool2_package_id"] = package_id
        states["pool2_storage_path"] = storage_path
        run.node_states_json = states
        _set_node_state(run, save_node_id, "completed", {"package_id": package_id})
        _set_node_state(run, table_node_id, "completed", None)
        _append_console(run, "info", "Workflow saved to Pool 2.")
        db.commit()

    @staticmethod
    async def resume_run(
        db: Session,
        run: WorkflowRun,
        *,
        approved_payload: dict[str, Any],
        skip_coa: bool = False,
        user_id: str,
    ) -> WorkflowRun:
        if run.run_status not in ("awaiting_review", "coa_running"):
            run_files = (
                db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
            )
            if run.run_status == "draft" and _promote_run_to_awaiting_review(run, run_files):
                db.commit()
            else:
                raise HTTPException(status_code=400, detail="Run is not awaiting review")

        task = db.query(ChatTask).filter(ChatTask.id == run.task_id).first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
        states["approved_payload"] = approved_payload
        states["skip_coa"] = skip_coa
        run.node_states_json = states
        record_node_execution(
            db,
            run,
            node_id="table",
            node_type="TableReview",
            status="completed",
            payload=approved_payload,
        )
        db.commit()

        from app.graph.executor import run_workflow_graph
        from app.graph.nodes.registry import NODE_CLASS_MAPPINGS
        from app.graph.workflow_events import workflow_event_hub

        async def _emit(event_type: str, payload: dict[str, Any]) -> None:
            payload = {**payload, "type": event_type}
            await workflow_event_hub.emit(run.id, payload)

        await run_workflow_graph(
            db, run, NODE_CLASS_MAPPINGS, emit=_emit, start_at="save"
        )
        await WorkflowService._transfer_to_module(db, run, approved_payload)
        db.refresh(run)
        return run

    @staticmethod
    async def _transfer_to_module(
        db: Session,
        run: WorkflowRun,
        approved_payload: dict[str, Any],
    ) -> None:
        """Persist approved rows into the destination module's transaction store.

        AR/AP rows -> ledger_transactions (tagged with the source module).
        BANK rows -> bank_transactions. Failures are logged, not fatal: the
        approved snapshot is already saved by the SaveResult node.
        """
        mode = (run.processing_mode or "").upper()
        try:
            if mode in ("AR", "AP"):
                rows = approved_payload.get("arapTransactions")
                if not isinstance(rows, list) or not rows:
                    return
                from app.api.reconciliation import (
                    LedgerImportRequest,
                    LedgerImportRow,
                    import_ledger_transactions,
                )

                import_rows = [
                    LedgerImportRow(
                        voucher_no=row.get("voucher_no") or row.get("id_number"),
                        transaction_type=row.get("transaction_type"),
                        amount=row.get("amount"),
                        currency=row.get("currency"),
                        date=row.get("date"),
                        payer=row.get("payer"),
                        payee=row.get("payee"),
                        bank=row.get("bank"),
                        memo=row.get("memo"),
                        category=row.get("category"),
                        client_row_id=str(row.get("id_number") or ""),
                        dr_cr=row.get("dr_cr"),
                    )
                    for row in rows
                    if isinstance(row, dict)
                ]
                req = LedgerImportRequest(module=mode, rows=import_rows)
                out = await import_ledger_transactions(
                    req, company_id=run.company_id, db=db
                )
                stored = out.get("stored_count") if isinstance(out, dict) else 0
                _append_console(run, "info", f"Transferred {stored} row(s) to {mode}.")
            elif mode == "BANK":
                rows = approved_payload.get("bankTransactions")
                if not isinstance(rows, list) or not rows:
                    return
                from app.api.bank_statements import _persist_bank_transactions

                result = {
                    "bank": approved_payload.get("bank") or "UNKNOWN",
                    "transactions": rows,
                    "count": len(rows),
                }
                out = _persist_bank_transactions(db, run.company_id, result)
                stored = out.get("stored_count") if isinstance(out, dict) else 0
                _append_console(run, "info", f"Transferred {stored} bank row(s).")
        except Exception as exc:  # pragma: no cover - defensive
            _append_console(run, "error", f"Transfer to module failed: {exc}")
            db.commit()

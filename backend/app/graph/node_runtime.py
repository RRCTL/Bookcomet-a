"""Node execution memory: file-level items, cache keys, Pool 2 artifact records."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services.pool2_storage import content_hash_json as _hash_json
from app.models.workflow import WorkflowNodeExecution, WorkflowRun
from app.services.pool2_storage import pool2


def _now() -> datetime:
    return datetime.now(timezone.utc)


def file_item_envelope(
    *,
    file_id: str,
    source_path: str | None,
    mode: str,
    lineage: list[str] | None = None,
    payload_ref: str | None = None,
) -> dict[str, Any]:
    return {
        "file_id": file_id,
        "source_path": source_path,
        "mode": mode,
        "lineage": lineage or [],
        "payload_ref": payload_ref,
    }


def cache_key_for_vlm(
    *,
    file_id: str,
    mode: str,
    provider: str,
    model: str | None,
    receipt_signal: str | None,
    table_preset: str | None,
    force_cross: bool,
    node_type: str = "VLM_API",
) -> str:
    payload = {
        "file_id": file_id,
        "mode": mode,
        "provider": provider,
        "model": model,
        "receipt_signal": receipt_signal,
        "table_preset": table_preset,
        "force_cross": force_cross,
        "node_type": node_type,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def find_cached_execution(
    db: Session,
    run_id: str,
    node_id: str,
    cache_key: str,
) -> WorkflowNodeExecution | None:
    return (
        db.query(WorkflowNodeExecution)
        .filter(
            WorkflowNodeExecution.run_id == run_id,
            WorkflowNodeExecution.node_id == node_id,
            WorkflowNodeExecution.cache_key == cache_key,
            WorkflowNodeExecution.status == "completed",
        )
        .order_by(WorkflowNodeExecution.created_at.desc())
        .first()
    )


def record_node_execution(
    db: Session,
    run: WorkflowRun,
    *,
    node_id: str,
    node_type: str,
    item_key: str | None = None,
    cache_key: str | None = None,
    status: str,
    error_text: str | None = None,
    duration_ms: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    payload: Any = None,
    input_hash: str | None = None,
) -> WorkflowNodeExecution:
    content_id: str | None = None
    storage_path: str | None = None
    if payload is not None:
        content_id, storage_path = pool2.save_node_output(
            run.company_id,
            run.id,
            node_id,
            payload,
        )
        if input_hash is None and cache_key:
            input_hash = cache_key

    row = WorkflowNodeExecution(
        id=str(uuid.uuid4()),
        run_id=run.id,
        company_id=run.company_id,
        node_id=node_id,
        node_type=node_type,
        item_key=item_key,
        cache_key=cache_key,
        input_hash=input_hash,
        content_id=content_id,
        storage_path=storage_path,
        status=status,
        error_text=(error_text or "")[:2000] if error_text else None,
        duration_ms=duration_ms,
        provider=provider,
        model=model,
    )
    db.add(row)
    db.commit()
    return row


def load_latest_node_payload(
    db: Session,
    run_id: str,
    node_id: str,
) -> Any | None:
    row = (
        db.query(WorkflowNodeExecution)
        .filter(
            WorkflowNodeExecution.run_id == run_id,
            WorkflowNodeExecution.node_id == node_id,
            WorkflowNodeExecution.status == "completed",
        )
        .order_by(WorkflowNodeExecution.created_at.desc(), WorkflowNodeExecution.id.desc())
        .first()
    )
    if not row or not row.storage_path:
        return None
    return pool2.load_node_output(row.storage_path)


def timed_execution(fn):
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        try:
            return await fn(*args, **kwargs)
        finally:
            pass

    return wrapper

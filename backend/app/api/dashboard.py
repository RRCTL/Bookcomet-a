"""
Dashboard Stats API — ROI metrics for the current company.

GET /api/dashboard/stats?period=week|month|all
  → Returns task counts, page counts, AI completion rate, and cost
    broken down by processing mode (AR / AP / BANK / OTHER).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id
from app.database import get_db
from app.models.chat import ChatTask
from app.models.compliance import OcrCompletionEvent
from app.models.memory import TokenUsageLog

router = APIRouter()

_DISPLAY_MODES = ["AR", "AP", "BANK", "OTHER"]


class ModeStats(BaseModel):
    tasks: int
    pages: int
    files: int


class DashboardStats(BaseModel):
    period: str
    total_tasks: int
    total_pages: int
    total_files: int
    ai_completion_rate: float | None   # 0.0–1.0, None if no data
    estimated_cost_usd: float
    by_mode: dict[str, ModeStats]      # keyed by processing_mode string


def _period_since(period: str) -> datetime:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if period == "week":
        since = now - timedelta(days=now.weekday())
        return since.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    # "all"
    return datetime(2000, 1, 1)


def _extract_mode_from_event(event: OcrCompletionEvent) -> str | None:
    """
    Pull the processing mode out of the nested OcrCompletionEvent metadata:
      metadata_json → decision_evidence → metadata → mode
    Returns None if the field is absent or not a recognised mode string.
    """
    try:
        meta = (event.metadata_json or {})
        de = meta.get("decision_evidence") or {}
        inner = de.get("metadata") or {}
        mode = (inner.get("mode") or "").upper().strip()
        return mode if mode in _DISPLAY_MODES else None
    except Exception:
        return None


@router.get("/api/dashboard/stats", response_model=DashboardStats, tags=["dashboard"])
async def get_dashboard_stats(
    period: str = Query("month", description="week | month | all"),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
) -> DashboardStats:
    since = _period_since(period)

    # ── Task & file counts (from ChatTask) ────────────────────────────────────
    tasks = (
        db.query(ChatTask)
        .filter(
            ChatTask.company_id == company_id,
            ChatTask.created_at >= since,
            ChatTask.deleted_at.is_(None),
        )
        .all()
    )

    total_tasks = len(tasks)
    total_files = sum(t.file_count or 0 for t in tasks)

    # Per-mode task & file counts (pages will be filled from OcrCompletionEvent below)
    by_mode: dict[str, ModeStats] = {}
    for task in tasks:
        mode = (task.processing_mode or "UNKNOWN").upper()
        if mode not in by_mode:
            by_mode[mode] = ModeStats(tasks=0, pages=0, files=0)
        by_mode[mode].tasks += 1
        by_mode[mode].files += task.file_count or 0

    # ── Page counts & AI rate from OcrCompletionEvent ─────────────────────────
    # One event per page per processing call — the only reliable historical source.
    ocr_events = (
        db.query(OcrCompletionEvent)
        .filter(
            OcrCompletionEvent.company_id == company_id,
            OcrCompletionEvent.created_at >= since,
        )
        .all()
    )

    ocr_complete_count = 0
    ai_complete_count = 0
    pages_by_mode: dict[str, int] = {}

    for event in ocr_events:
        if event.stage == "ocr_complete":
            ocr_complete_count += 1
            mode = _extract_mode_from_event(event)
            if mode:
                pages_by_mode[mode] = pages_by_mode.get(mode, 0) + 1
        elif event.stage == "ai_complete":
            ai_complete_count += 1

    total_pages = ocr_complete_count

    ai_rate: float | None = None
    if ocr_complete_count > 0:
        ai_rate = round(ai_complete_count / ocr_complete_count, 4)

    # Merge page counts into by_mode, ensure all 4 display modes are present
    for m in _DISPLAY_MODES:
        if m not in by_mode:
            by_mode[m] = ModeStats(tasks=0, pages=0, files=0)
        by_mode[m].pages = pages_by_mode.get(m, 0)

    # ── Token cost from TokenUsageLog ─────────────────────────────────────────
    token_rows = (
        db.query(TokenUsageLog)
        .filter(
            TokenUsageLog.company_id == company_id,
            TokenUsageLog.created_at >= since,
        )
        .all()
    )
    estimated_cost = round(sum(r.estimated_cost_usd or 0.0 for r in token_rows), 6)

    return DashboardStats(
        period=period,
        total_tasks=total_tasks,
        total_pages=total_pages,
        total_files=total_files,
        ai_completion_rate=ai_rate,
        estimated_cost_usd=estimated_cost,
        by_mode=by_mode,
    )

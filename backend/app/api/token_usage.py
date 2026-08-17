"""
Token Usage API — exposes LLM cost/usage aggregates for the frontend indicator.

GET /api/token-usage?period=month   → aggregate for current month
GET /api/token-usage?period=week    → aggregate for current week
GET /api/token-usage?period=session&task_id=X → aggregate for a single task
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id
from app.database import get_db
from app.models.memory import TokenUsageLog

router = APIRouter()


class TokenUsageSummary(BaseModel):
    period: str
    total_calls: int
    total_tokens: int
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_usd: float
    breakdown: list[dict]   # per call_type breakdown


@router.get("/api/token-usage", response_model=TokenUsageSummary, tags=["token-usage"])
async def get_token_usage(
    period: str = Query("month", description="month | week | session"),
    task_id: str | None = Query(None, description="Required when period=session"),
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
) -> TokenUsageSummary:
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    if period == "month":
        since = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        since = now - timedelta(days=now.weekday())
        since = since.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        since = datetime(2000, 1, 1)

    base_q = db.query(TokenUsageLog).filter(
        TokenUsageLog.company_id == company_id,
        TokenUsageLog.created_at >= since,
    )
    if period == "session" and task_id:
        base_q = base_q.filter(TokenUsageLog.task_id == task_id)

    rows = base_q.all()

    total_calls = len(rows)
    total_tokens = sum(r.total_tokens or 0 for r in rows)
    prompt_tokens = sum(r.prompt_tokens or 0 for r in rows)
    completion_tokens = sum(r.completion_tokens or 0 for r in rows)
    estimated_cost = round(sum(r.estimated_cost_usd or 0.0 for r in rows), 6)

    # Per call_type breakdown
    breakdown_map: dict[str, dict] = {}
    for r in rows:
        ct = r.call_type or "unknown"
        if ct not in breakdown_map:
            breakdown_map[ct] = {
                "call_type": ct,
                "calls": 0,
                "total_tokens": 0,
                "estimated_cost_usd": 0.0,
            }
        breakdown_map[ct]["calls"] += 1
        breakdown_map[ct]["total_tokens"] += r.total_tokens or 0
        breakdown_map[ct]["estimated_cost_usd"] = round(
            breakdown_map[ct]["estimated_cost_usd"] + (r.estimated_cost_usd or 0.0), 6
        )

    return TokenUsageSummary(
        period=period,
        total_calls=total_calls,
        total_tokens=total_tokens,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        estimated_cost_usd=estimated_cost,
        breakdown=list(breakdown_map.values()),
    )

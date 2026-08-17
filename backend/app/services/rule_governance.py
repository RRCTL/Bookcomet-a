import re
from datetime import datetime, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.company_context import CompanyRule
from app.models.rule_events import CompanyRuleAuditLog, CompanyRuleHitEvent

RULE_TYPE_COMPANY_CUSTOM = "company_custom"
RULE_TYPE_KNOWLEDGE_ARTICLE = "knowledge_article"
RULE_TYPE_COMPANY_CONTEXT = "company_context"
RULE_TYPE_DOCUMENT_GATE = "document_gate"

MAX_KNOWLEDGE_ARTICLE_BODY = 2000
MAX_COMPANY_CONTEXT_BODY = 50000


def normalize_pattern(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def validate_rule_payload(
    *,
    rule_name: str,
    keyword_pattern: str | None,
    vendor_pattern: str | None,
    amount_pattern: str | None,
) -> None:
    if not rule_name or not rule_name.strip():
        raise HTTPException(status_code=400, detail="rule_name is required")

    normalized_keyword = normalize_pattern(keyword_pattern)
    normalized_vendor = normalize_pattern(vendor_pattern)
    normalized_amount = normalize_pattern(amount_pattern)
    if not any([normalized_keyword, normalized_vendor, normalized_amount]):
        raise HTTPException(
            status_code=400,
            detail="At least one of keyword_pattern/vendor_pattern/amount_pattern is required",
        )

    if normalized_keyword:
        tokens = [t.strip() for t in re.split(r"[,;/|]+", normalized_keyword) if t.strip()]
        if not tokens:
            raise HTTPException(status_code=400, detail="keyword_pattern is too broad")
        too_short = [token for token in tokens if len(token) < 2]
        if too_short:
            raise HTTPException(
                status_code=400,
                detail="keyword_pattern contains too-short tokens; use at least 2 chars",
            )


def validate_knowledge_article_payload(
    *,
    rule_name: str,
    body: str,
    use_when: str | None = None,
) -> None:
    if not rule_name or not rule_name.strip():
        raise HTTPException(status_code=400, detail="rule_name is required")
    text = (body or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="content is required")
    if len(text) > MAX_KNOWLEDGE_ARTICLE_BODY:
        raise HTTPException(
            status_code=400,
            detail=f"content must be at most {MAX_KNOWLEDGE_ARTICLE_BODY} characters",
        )
    if use_when is not None and len(use_when.strip()) > 500:
        raise HTTPException(status_code=400, detail="use_when is too long")


def validate_company_context_payload(*, body: str, use_when: str | None = None) -> None:
    text = (body or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="body is required")
    if len(text) > MAX_COMPANY_CONTEXT_BODY:
        raise HTTPException(
            status_code=400,
            detail=f"body must be at most {MAX_COMPANY_CONTEXT_BODY} characters",
        )
    if use_when is not None and len(use_when.strip()) > 500:
        raise HTTPException(status_code=400, detail="use_when is too long")


def ensure_no_duplicate_rule(
    db: Session,
    *,
    company_id: str,
    rule_name: str,
    keyword_pattern: str | None,
    document_type: str | None,
    exclude_rule_id: str | None = None,
) -> None:
    query = db.query(CompanyRule).filter(
        CompanyRule.company_id == company_id,
        func.lower(CompanyRule.rule_name) == rule_name.strip().lower(),
    )
    if document_type:
        query = query.filter(func.lower(CompanyRule.document_type) == document_type.lower())
    else:
        query = query.filter(CompanyRule.document_type.is_(None))
    if exclude_rule_id:
        query = query.filter(CompanyRule.id != exclude_rule_id)

    existing = query.first()
    if existing:
        raise HTTPException(status_code=409, detail="Duplicate rule name for the same document type")

    normalized_keyword = normalize_pattern(keyword_pattern)
    if normalized_keyword:
        query2 = db.query(CompanyRule).filter(
            CompanyRule.company_id == company_id,
            func.lower(CompanyRule.keyword_pattern) == normalized_keyword.lower(),
        )
        if document_type:
            query2 = query2.filter(func.lower(CompanyRule.document_type) == document_type.lower())
        else:
            query2 = query2.filter(CompanyRule.document_type.is_(None))
        if exclude_rule_id:
            query2 = query2.filter(CompanyRule.id != exclude_rule_id)
        existing_keyword = query2.first()
        if existing_keyword:
            raise HTTPException(status_code=409, detail="Duplicate keyword_pattern for the same document type")


def write_rule_audit_log(
    db: Session,
    *,
    company_id: str,
    action: str,
    actor_user_id: str | None,
    trace_id: str | None = None,
    rule_id: str | None = None,
    before_json: dict[str, Any] | None = None,
    after_json: dict[str, Any] | None = None,
    metadata_json: dict[str, Any] | None = None,
) -> None:
    db.add(
        CompanyRuleAuditLog(
            company_id=company_id,
            trace_id=trace_id,
            rule_id=rule_id,
            action=action,
            actor_user_id=actor_user_id,
            before_json=before_json,
            after_json=after_json,
            metadata_json=metadata_json,
        )
    )


def summarize_rule_performance(db: Session, *, company_id: str) -> dict[str, Any]:
    total = db.query(CompanyRule).filter(CompanyRule.company_id == company_id).count()
    active = db.query(CompanyRule).filter(
        CompanyRule.company_id == company_id, CompanyRule.is_active.is_(True)
    ).count()
    never_hit = db.query(CompanyRule).filter(
        CompanyRule.company_id == company_id, CompanyRule.hit_count <= 0
    ).count()

    now = datetime.utcnow()
    cut7 = now - timedelta(days=7)
    cut30 = now - timedelta(days=30)
    hit7 = db.query(CompanyRuleHitEvent).filter(
        CompanyRuleHitEvent.company_id == company_id, CompanyRuleHitEvent.created_at >= cut7
    ).count()
    hit30 = db.query(CompanyRuleHitEvent).filter(
        CompanyRuleHitEvent.company_id == company_id, CompanyRuleHitEvent.created_at >= cut30
    ).count()

    top_rows = db.query(CompanyRule).filter(CompanyRule.company_id == company_id).order_by(
        CompanyRule.hit_count.desc(), CompanyRule.priority.asc()
    ).limit(10).all()
    top_rules = [
        {
            "id": r.id,
            "rule_name": r.rule_name,
            "hit_count": int(r.hit_count or 0),
            "last_hit_at": r.last_hit_at.isoformat() if r.last_hit_at else None,
        }
        for r in top_rows
    ]

    stale_rows = db.query(CompanyRule).filter(
        CompanyRule.company_id == company_id,
        CompanyRule.is_active.is_(True),
        CompanyRule.last_hit_at.is_not(None),
        CompanyRule.last_hit_at < cut30,
    ).order_by(CompanyRule.last_hit_at.asc()).limit(10).all()
    stale_rules = [
        {
            "id": r.id,
            "rule_name": r.rule_name,
            "last_hit_at": r.last_hit_at.isoformat() if r.last_hit_at else None,
            "hit_count": int(r.hit_count or 0),
        }
        for r in stale_rows
    ]

    return {
        "total_rules": total,
        "active_rules": active,
        "never_hit_rules": never_hit,
        "hit_events_7d": hit7,
        "hit_events_30d": hit30,
        "top_rules": top_rules,
        "stale_rules": stale_rules,
    }

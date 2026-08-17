"""
Exclusion Rules API
===================
GET    /company/exclusions          — list all rules for this company
POST   /company/exclusions          — create a new rule
PATCH  /company/exclusions/{id}     — update (enable/disable or edit)
DELETE /company/exclusions/{id}     — delete a rule
POST   /company/exclusions/test     — test patterns against sample text (preview)

Security: company_id comes from JWT/header dependency only.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id
from app.core.text_limits import (
    MAX_EXCLUSION_MODES_CHARS,
    MAX_EXCLUSION_PATTERN_CHARS,
    MAX_EXCLUSION_REASON_CHARS,
    MAX_EXCLUSION_TEST_SAMPLE_CHARS,
    MAX_EXCLUSION_VENDOR_FIELD_CHARS,
)
from app.database import get_db
from app.models.exclusion_rule import ExclusionRule

router = APIRouter()

VALID_PATTERN_TYPES = {"keyword", "vendor", "amount"}


# ── Request / Response models ─────────────────────────────────────────────────

class ExclusionRuleCreate(BaseModel):
    pattern: str = Field(..., max_length=MAX_EXCLUSION_PATTERN_CHARS)
    pattern_type: str = Field(default="keyword", max_length=32)  # keyword | vendor | amount
    reason: Optional[str] = Field(default=None, max_length=MAX_EXCLUSION_REASON_CHARS)
    modes: Optional[str] = Field(default=None, max_length=MAX_EXCLUSION_MODES_CHARS)


class ExclusionRulePatch(BaseModel):
    pattern: Optional[str] = Field(default=None, max_length=MAX_EXCLUSION_PATTERN_CHARS)
    pattern_type: Optional[str] = Field(default=None, max_length=32)
    reason: Optional[str] = Field(default=None, max_length=MAX_EXCLUSION_REASON_CHARS)
    modes: Optional[str] = Field(default=None, max_length=MAX_EXCLUSION_MODES_CHARS)
    is_active: Optional[bool] = None


class ExclusionTestRequest(BaseModel):
    sample_text: str = Field(..., max_length=MAX_EXCLUSION_TEST_SAMPLE_CHARS)
    amount: Optional[float] = None
    vendor: Optional[str] = Field(default=None, max_length=MAX_EXCLUSION_VENDOR_FIELD_CHARS)
    mode: Optional[str] = Field(default=None, max_length=64)


class ExclusionRuleResponse(BaseModel):
    id: str
    pattern: str
    pattern_type: str
    reason: Optional[str]
    modes: Optional[str]
    is_active: bool
    hit_count: int
    last_hit_at: Optional[str]
    created_at: Optional[str]


class ExclusionDeleteResponse(BaseModel):
    status: str
    deleted_id: str


class ExclusionTestResponse(BaseModel):
    would_flag: bool
    matched_rules: list[dict]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _rule_to_dict(r: ExclusionRule) -> dict:
    return {
        "id": r.id,
        "pattern": r.pattern,
        "pattern_type": r.pattern_type,
        "reason": r.reason,
        "modes": r.modes,
        "is_active": r.is_active,
        "hit_count": r.hit_count,
        "last_hit_at": r.last_hit_at.isoformat() if r.last_hit_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/company/exclusions", response_model=list[ExclusionRuleResponse])
async def list_exclusion_rules(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Return all exclusion rules for this company."""
    rules = (
        db.query(ExclusionRule)
        .filter(ExclusionRule.company_id == company_id)
        .order_by(ExclusionRule.created_at.desc())
        .all()
    )
    return [_rule_to_dict(r) for r in rules]


@router.post("/company/exclusions", response_model=ExclusionRuleResponse, status_code=201)
async def create_exclusion_rule(
    payload: ExclusionRuleCreate,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Create a new exclusion rule."""
    if payload.pattern_type not in VALID_PATTERN_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid pattern_type. Valid: {sorted(VALID_PATTERN_TYPES)}",
        )
    if not payload.pattern.strip():
        raise HTTPException(status_code=400, detail="Pattern cannot be empty.")

    rule = ExclusionRule(
        id=str(uuid.uuid4()),
        company_id=company_id,
        pattern=payload.pattern.strip(),
        pattern_type=payload.pattern_type,
        reason=payload.reason,
        modes=payload.modes,
        is_active=True,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _rule_to_dict(rule)


@router.patch("/company/exclusions/{rule_id}", response_model=ExclusionRuleResponse)
async def update_exclusion_rule(
    rule_id: str,
    payload: ExclusionRulePatch,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Update an existing exclusion rule."""
    rule = (
        db.query(ExclusionRule)
        .filter(ExclusionRule.id == rule_id, ExclusionRule.company_id == company_id)
        .first()
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found.")

    if payload.pattern is not None:
        if not payload.pattern.strip():
            raise HTTPException(status_code=400, detail="Pattern cannot be empty.")
        rule.pattern = payload.pattern.strip()
    if payload.pattern_type is not None:
        if payload.pattern_type not in VALID_PATTERN_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid pattern_type.")
        rule.pattern_type = payload.pattern_type
    if payload.reason is not None:
        rule.reason = payload.reason
    if payload.modes is not None:
        rule.modes = payload.modes
    if payload.is_active is not None:
        rule.is_active = payload.is_active

    db.commit()
    db.refresh(rule)
    return _rule_to_dict(rule)


@router.delete("/company/exclusions/{rule_id}", response_model=ExclusionDeleteResponse)
async def delete_exclusion_rule(
    rule_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Delete an exclusion rule."""
    rule = (
        db.query(ExclusionRule)
        .filter(ExclusionRule.id == rule_id, ExclusionRule.company_id == company_id)
        .first()
    )
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found.")
    db.delete(rule)
    db.commit()
    return {"status": "ok", "deleted_id": rule_id}


@router.post("/company/exclusions/test", response_model=ExclusionTestResponse)
async def test_exclusion_rules(
    payload: ExclusionTestRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """
    Test which exclusion rules would fire for a given sample text/vendor/amount.
    Returns matched rule IDs with their reasons.
    """
    from app.services.exclusion_service import check_row_exclusions

    rules = (
        db.query(ExclusionRule)
        .filter(ExclusionRule.company_id == company_id, ExclusionRule.is_active == True)  # noqa
        .all()
    )

    sample_row = {
        "payer": payload.vendor or "",
        "payee": payload.vendor or "",
        "amount": payload.amount,
    }

    matched = check_row_exclusions(sample_row, rules, payload.sample_text, payload.mode)
    return {
        "would_flag": bool(matched),
        "matched_rules": matched,
    }

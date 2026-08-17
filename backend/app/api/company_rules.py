"""
Company classification rules (CompanyRule ORM) — reconciliation AI hints + Knowledge.

GET    /company/classification-rules
POST   /company/classification-rules
PUT    /company/classification-rules/company-context — singleton upsert
PATCH  /company/classification-rules/{rule_id}
DELETE /company/classification-rules/{rule_id}
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import case
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user_id, get_trace_id
from app.database import get_db
from app.models.company_context import CompanyRule
from app.services.rule_governance import (
    RULE_TYPE_COMPANY_CONTEXT,
    RULE_TYPE_KNOWLEDGE_ARTICLE,
    ensure_no_duplicate_rule,
    validate_company_context_payload,
    validate_knowledge_article_payload,
    validate_rule_payload,
    write_rule_audit_log,
)

router = APIRouter()

DEFAULT_CONTEXT_RULE_NAME = "Business context"


class CompanyRuleCreate(BaseModel):
    rule_type: str = Field(default="company_custom", pattern="^(company_custom|knowledge_article)$")
    rule_name: str = Field(..., min_length=1)
    pattern_type: Optional[str] = Field(None, pattern="^(keyword|vendor|amount)$")
    pattern: Optional[str] = None
    use_when: Optional[str] = None
    content: Optional[str] = None
    notes: Optional[str] = None
    document_type: Optional[str] = None
    priority: int = 100


class CompanyContextUpsert(BaseModel):
    use_when: Optional[str] = None
    body: str = Field(..., min_length=1)


class CompanyRulePatch(BaseModel):
    rule_name: Optional[str] = None
    pattern_type: Optional[str] = Field(None, pattern="^(keyword|vendor|amount)$")
    pattern: Optional[str] = None
    notes: Optional[str] = None
    use_when: Optional[str] = None
    content: Optional[str] = None
    document_type: Optional[str] = None
    is_active: Optional[bool] = None
    priority: Optional[int] = None


def _rule_json_dict(r: CompanyRule) -> dict[str, Any]:
    if isinstance(r.rule_json, dict):
        return r.rule_json
    return {}


def _serialize_rule(r: CompanyRule) -> dict[str, Any]:
    rj = _rule_json_dict(r)
    ptype = (
        "keyword"
        if r.keyword_pattern
        else "vendor"
        if r.vendor_pattern
        else "amount"
        if r.amount_pattern
        else "keyword"
    )
    pat = r.keyword_pattern or r.vendor_pattern or r.amount_pattern or ""
    use_when = rj.get("use_when")
    body = rj.get("body")
    return {
        "id": r.id,
        "rule_name": r.rule_name,
        "rule_type": r.rule_type,
        "pattern_type": ptype,
        "keyword_pattern": r.keyword_pattern,
        "vendor_pattern": r.vendor_pattern,
        "amount_pattern": r.amount_pattern,
        "pattern": pat,
        "document_type": r.document_type,
        "notes": r.notes,
        "rule_json": rj,
        "use_when": use_when if isinstance(use_when, str) else None,
        "content": body if isinstance(body, str) else None,
        "priority": r.priority,
        "is_active": r.is_active,
        "hit_count": int(r.hit_count or 0),
        "last_hit_at": r.last_hit_at.isoformat() if r.last_hit_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


@router.get("/company/classification-rules")
async def list_classification_rules(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    rt_order = case(
        (CompanyRule.rule_type == RULE_TYPE_COMPANY_CONTEXT, 0),
        else_=1,
    )
    rows = (
        db.query(CompanyRule)
        .filter(CompanyRule.company_id == company_id)
        .order_by(rt_order, CompanyRule.priority.asc(), CompanyRule.created_at.desc())
        .all()
    )
    return {"rules": [_serialize_rule(r) for r in rows]}


@router.put("/company/classification-rules/company-context")
async def upsert_company_context_rule(
    payload: CompanyContextUpsert,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    trace_id: str = Depends(get_trace_id),
):
    uw = (payload.use_when or "").strip() or None
    validate_company_context_payload(body=payload.body, use_when=payload.use_when)

    row = (
        db.query(CompanyRule)
        .filter(
            CompanyRule.company_id == company_id,
            CompanyRule.rule_type == RULE_TYPE_COMPANY_CONTEXT,
        )
        .first()
    )
    body_text = payload.body.strip()

    if row is None:
        row = CompanyRule(
            id=str(uuid.uuid4()),
            company_id=company_id,
            rule_name=DEFAULT_CONTEXT_RULE_NAME,
            rule_type=RULE_TYPE_COMPANY_CONTEXT,
            vendor_pattern=None,
            keyword_pattern=None,
            amount_pattern=None,
            document_type=None,
            rule_json={"use_when": uw, "body": body_text},
            priority=0,
            is_active=True,
            notes=None,
            created_by=user_id,
        )
        db.add(row)
        write_rule_audit_log(
            db,
            company_id=company_id,
            action="create",
            actor_user_id=user_id,
            trace_id=trace_id,
            rule_id=row.id,
            after_json=_serialize_rule(row),
        )
    else:
        before = _serialize_rule(row)
        row.rule_json = {"use_when": uw, "body": body_text}
        write_rule_audit_log(
            db,
            company_id=company_id,
            action="update",
            actor_user_id=user_id,
            trace_id=trace_id,
            rule_id=row.id,
            before_json=before,
            after_json=_serialize_rule(row),
        )
    db.commit()
    db.refresh(row)
    return _serialize_rule(row)


@router.post("/company/classification-rules")
async def create_classification_rule(
    payload: CompanyRuleCreate,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    trace_id: str = Depends(get_trace_id),
):
    if payload.rule_type == RULE_TYPE_KNOWLEDGE_ARTICLE:
        body = (payload.content or "").strip()
        uw = (payload.use_when or "").strip() or None
        validate_knowledge_article_payload(
            rule_name=payload.rule_name,
            body=body,
            use_when=payload.use_when,
        )
        ensure_no_duplicate_rule(
            db,
            company_id=company_id,
            rule_name=payload.rule_name,
            keyword_pattern=None,
            document_type=payload.document_type,
        )
        row = CompanyRule(
            id=str(uuid.uuid4()),
            company_id=company_id,
            rule_name=payload.rule_name.strip(),
            rule_type=RULE_TYPE_KNOWLEDGE_ARTICLE,
            vendor_pattern=None,
            keyword_pattern=None,
            amount_pattern=None,
            document_type=payload.document_type.strip() if payload.document_type else None,
            rule_json={"use_when": uw, "body": body},
            priority=payload.priority,
            is_active=True,
            notes=(payload.notes or "").strip() or None,
            created_by=user_id,
        )
        db.add(row)
        write_rule_audit_log(
            db,
            company_id=company_id,
            action="create",
            actor_user_id=user_id,
            trace_id=trace_id,
            rule_id=row.id,
            after_json=_serialize_rule(row),
        )
        db.commit()
        db.refresh(row)
        return _serialize_rule(row)

    if not payload.pattern_type or payload.pattern is None:
        raise HTTPException(status_code=400, detail="pattern_type and pattern are required")
    kw = payload.pattern.strip() if payload.pattern_type == "keyword" else None
    vp = payload.pattern.strip() if payload.pattern_type == "vendor" else None
    ap = payload.pattern.strip() if payload.pattern_type == "amount" else None

    validate_rule_payload(
        rule_name=payload.rule_name,
        keyword_pattern=kw,
        vendor_pattern=vp,
        amount_pattern=ap,
    )
    ensure_no_duplicate_rule(
        db,
        company_id=company_id,
        rule_name=payload.rule_name,
        keyword_pattern=kw,
        document_type=payload.document_type,
    )

    row = CompanyRule(
        id=str(uuid.uuid4()),
        company_id=company_id,
        rule_name=payload.rule_name.strip(),
        rule_type="company_custom",
        vendor_pattern=vp,
        keyword_pattern=kw,
        amount_pattern=ap,
        document_type=payload.document_type.strip() if payload.document_type else None,
        rule_json={},
        priority=payload.priority,
        is_active=True,
        notes=(payload.notes or "").strip() or None,
        created_by=user_id,
    )
    db.add(row)
    write_rule_audit_log(
        db,
        company_id=company_id,
        action="create",
        actor_user_id=user_id,
        trace_id=trace_id,
        rule_id=row.id,
        after_json=_serialize_rule(row),
    )
    db.commit()
    db.refresh(row)
    return _serialize_rule(row)


@router.patch("/company/classification-rules/{rule_id}")
async def patch_classification_rule(
    rule_id: str,
    payload: CompanyRulePatch,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    trace_id: str = Depends(get_trace_id),
):
    row = (
        db.query(CompanyRule)
        .filter(CompanyRule.id == rule_id, CompanyRule.company_id == company_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    before = _serialize_rule(row)

    if payload.is_active is not None:
        row.is_active = payload.is_active
    if payload.priority is not None:
        row.priority = payload.priority
    if payload.notes is not None:
        row.notes = payload.notes.strip() or None
    if payload.document_type is not None:
        row.document_type = payload.document_type.strip() or None

    if payload.rule_name is not None and payload.rule_name.strip():
        new_name = payload.rule_name.strip()
        if new_name != row.rule_name:
            ensure_no_duplicate_rule(
                db,
                company_id=company_id,
                rule_name=new_name,
                keyword_pattern=row.keyword_pattern,
                document_type=row.document_type,
                exclude_rule_id=row.id,
            )
        row.rule_name = new_name

    rj = dict(_rule_json_dict(row))

    if row.rule_type in (RULE_TYPE_KNOWLEDGE_ARTICLE, RULE_TYPE_COMPANY_CONTEXT):
        if payload.use_when is not None:
            rj["use_when"] = (payload.use_when or "").strip() or None
        if payload.content is not None:
            body = (payload.content or "").strip()
            if row.rule_type == RULE_TYPE_COMPANY_CONTEXT:
                validate_company_context_payload(body=body, use_when=rj.get("use_when"))
            else:
                validate_knowledge_article_payload(
                    rule_name=row.rule_name,
                    body=body,
                    use_when=rj.get("use_when"),
                )
            rj["body"] = body
        elif payload.use_when is not None:
            existing = (rj.get("body") or "").strip()
            if row.rule_type == RULE_TYPE_COMPANY_CONTEXT:
                validate_company_context_payload(body=existing, use_when=rj.get("use_when"))
            else:
                validate_knowledge_article_payload(
                    rule_name=row.rule_name,
                    body=existing,
                    use_when=rj.get("use_when"),
                )
        row.rule_json = rj
    elif payload.pattern_type is not None and payload.pattern is not None:
        kw = vp = ap = None
        if payload.pattern_type == "keyword":
            kw = payload.pattern.strip()
        elif payload.pattern_type == "vendor":
            vp = payload.pattern.strip()
        else:
            ap = payload.pattern.strip()
        validate_rule_payload(
            rule_name=row.rule_name,
            keyword_pattern=kw,
            vendor_pattern=vp,
            amount_pattern=ap,
        )
        ensure_no_duplicate_rule(
            db,
            company_id=company_id,
            rule_name=row.rule_name,
            keyword_pattern=kw,
            document_type=row.document_type,
            exclude_rule_id=row.id,
        )
        row.keyword_pattern = kw
        row.vendor_pattern = vp
        row.amount_pattern = ap

    write_rule_audit_log(
        db,
        company_id=company_id,
        action="update",
        actor_user_id=user_id,
        trace_id=trace_id,
        rule_id=row.id,
        before_json=before,
        after_json=_serialize_rule(row),
    )
    db.commit()
    db.refresh(row)
    return _serialize_rule(row)


@router.delete("/company/classification-rules/{rule_id}")
async def delete_classification_rule(
    rule_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
    user_id: str = Depends(get_current_user_id),
    trace_id: str = Depends(get_trace_id),
):
    row = (
        db.query(CompanyRule)
        .filter(CompanyRule.id == rule_id, CompanyRule.company_id == company_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    if row.rule_type == "document_gate":
        raise HTTPException(
            status_code=400,
            detail="Document gate rules cannot be deleted via this API",
        )
    if row.rule_type == RULE_TYPE_COMPANY_CONTEXT:
        raise HTTPException(
            status_code=400,
            detail="Business context cannot be deleted; disable or clear it in Settings",
        )
    before = _serialize_rule(row)
    write_rule_audit_log(
        db,
        company_id=company_id,
        action="delete",
        actor_user_id=user_id,
        trace_id=trace_id,
        rule_id=row.id,
        before_json=before,
    )
    db.delete(row)
    db.commit()
    return {"status": "ok", "deleted_id": rule_id}

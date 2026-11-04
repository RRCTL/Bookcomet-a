"""
Company Rule Memory API
========================
GET  /company/memory/{mode}            — get MD content + version
PUT  /company/memory/{mode}            — save updated MD (with version check)
GET  /company/memory/{mode}/history    — list last 5 version snapshots
POST /company/memory/{mode}/restore/{version_num} — restore a past version
GET  /company/memory/export            — export all mode MD files as a zip
POST /company/memory/import            — import MD content
POST /company/memory/{mode}/generate   — AI-generates starter rules from business description

Security: company_id always comes from the JWT/header dependency, never from request body.
"""
from __future__ import annotations

import io
import json
import os
import uuid
import zipfile
from datetime import datetime
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user_id
from app.core.gateway_settings import openai_chat_completions_url
from app.core.config import settings
from app.core.text_limits import (
    MAX_COMPANY_NAME_CHARS,
    MAX_MARKDOWN_BODY_CHARS,
    MAX_RULE_MEMORY_GENERATE_DESCRIPTION_CHARS,
    MAX_RULE_MEMORY_IMPORT_ITEMS,
)
from app.database import get_db
from app.core.processing_mode import normalize_processing_mode
from app.models.rule_memory import VALID_MODES, MAX_VERSIONS, CompanyRuleMemory, CompanyRuleMemoryVersion
from app.models.company_context import CompanyProfile
from app.services.rule_memory_parser import build_empty_md, parse_rules
from app.services.rule_memory_templates import get_starter_template, get_ai_generated_template
from app.services.abuse_guard import (
    check_generation_rate_async,
    check_monthly_cost,
    normalise_input,
    scan_output,
    build_hardened_system_prompt,
    _MAX_GEN_CHARS,
)

router = APIRouter()

_DEPLOY_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
_DEPLOY_BASE_URL = (
    os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or os.getenv("VLM_BASE_URL") or ""
).rstrip("/")


# ── Request / Response models ─────────────────────────────────────────────────

class RuleMemorySaveRequest(BaseModel):
    content: str = Field(..., max_length=MAX_MARKDOWN_BODY_CHARS)
    version: int  # optimistic concurrency — must match current version


class RuleMemoryImportRequest(BaseModel):
    memories: list[dict]   # [{mode: str, content: str}]

    @model_validator(mode="after")
    def _import_size(self):
        if len(self.memories) > MAX_RULE_MEMORY_IMPORT_ITEMS:
            raise ValueError(f"at most {MAX_RULE_MEMORY_IMPORT_ITEMS} memories per import")
        for m in self.memories:
            if not isinstance(m, dict):
                continue
            c = m.get("content")
            if isinstance(c, str) and len(c) > MAX_MARKDOWN_BODY_CHARS:
                raise ValueError("import memory content too long")
        return self


class RuleMemoryGenerateRequest(BaseModel):
    business_description: str = Field(..., max_length=MAX_RULE_MEMORY_GENERATE_DESCRIPTION_CHARS)
    company_name: Optional[str] = Field(default=None, max_length=MAX_COMPANY_NAME_CHARS)


class RuleMemoryResponse(BaseModel):
    company_id: str
    mode: str
    content: str
    version: int
    updated_at: Optional[str]
    updated_by_user: Optional[str]
    updated_by_type: Optional[str]
    is_active: bool = True


class RuleMemoryActiveRequest(BaseModel):
    is_active: bool


class RuleMemorySaveResponse(BaseModel):
    status: str
    mode: str
    version: int
    updated_at: Optional[str]


class RuleMemoryVersionItem(BaseModel):
    version: int
    saved_at: Optional[str]
    saved_by: Optional[str]
    saved_by_type: Optional[str]
    content_preview: str


class RuleMemoryRestoreResponse(BaseModel):
    status: str
    restored_from_version: int
    new_version: int


class RuleMemoryImportResponse(BaseModel):
    status: str
    imported_modes: list[str]


class RuleMemoryGenerateResponse(BaseModel):
    status: str
    mode: str
    version: int
    content: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_memory(
    db: Session,
    company_id: str,
    mode: str,
    company_name: str = "",
) -> CompanyRuleMemory:
    """Fetch or auto-create the memory row for (company_id, mode)."""
    row = db.query(CompanyRuleMemory).filter(
        CompanyRuleMemory.company_id == company_id,
        CompanyRuleMemory.mode == mode,
    ).first()
    if row is None:
        content = get_starter_template(mode, company_name)
        row = CompanyRuleMemory(
            id=str(uuid.uuid4()),
            company_id=company_id,
            mode=mode,
            content=content,
            version=1,
            updated_by_type="system",
            is_active=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _save_version_snapshot(
    db: Session,
    memory: CompanyRuleMemory,
    saved_by: str | None,
    saved_by_type: str,
) -> None:
    """Write a version snapshot and purge older ones beyond MAX_VERSIONS."""
    snap = CompanyRuleMemoryVersion(
        id=str(uuid.uuid4()),
        memory_id=memory.id,
        company_id=memory.company_id,
        mode=memory.mode,
        version=memory.version,
        content=memory.content,
        saved_by=saved_by,
        saved_by_type=saved_by_type,
    )
    db.add(snap)
    db.flush()

    # Purge oldest snapshots if over limit
    all_versions = (
        db.query(CompanyRuleMemoryVersion)
        .filter(CompanyRuleMemoryVersion.memory_id == memory.id)
        .order_by(CompanyRuleMemoryVersion.version.desc())
        .all()
    )
    if len(all_versions) > MAX_VERSIONS:
        for old in all_versions[MAX_VERSIONS:]:
            db.delete(old)


def _get_company_name(db: Session, company_id: str) -> str:
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    if profile and profile.company_name:
        return profile.company_name
    settings = (profile.custom_settings or {}) if profile else {}
    return settings.get("company_name", "") if isinstance(settings, dict) else ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/company/memory/{mode}", response_model=RuleMemoryResponse)
async def get_rule_memory(
    mode: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    mode = normalize_processing_mode(mode)
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Valid: {sorted(VALID_MODES)}")

    company_name = _get_company_name(db, company_id)
    row = _get_or_create_memory(db, company_id, mode, company_name)
    return {
        "company_id": company_id,
        "mode": mode,
        "content": row.content,
        "version": row.version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by_user": row.updated_by_user,
        "updated_by_type": row.updated_by_type,
        "is_active": bool(getattr(row, "is_active", True)),
    }


@router.patch("/company/memory/{mode}/active")
async def patch_rule_memory_active(
    mode: str,
    payload: RuleMemoryActiveRequest,
    company_id: str = Depends(get_current_company_id),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    mode = normalize_processing_mode(mode)
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Valid: {sorted(VALID_MODES)}")

    company_name = _get_company_name(db, company_id)
    row = _get_or_create_memory(db, company_id, mode, company_name)
    row.is_active = payload.is_active
    row.updated_by_user = user_id
    row.updated_by_type = "user"
    db.commit()
    db.refresh(row)
    return {
        "status": "ok",
        "mode": mode,
        "is_active": row.is_active,
        "version": row.version,
    }


@router.put("/company/memory/{mode}", response_model=RuleMemorySaveResponse)
async def save_rule_memory(
    mode: str,
    payload: RuleMemorySaveRequest,
    company_id: str = Depends(get_current_company_id),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    mode = normalize_processing_mode(mode)
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Valid: {sorted(VALID_MODES)}")

    company_name = _get_company_name(db, company_id)
    row = _get_or_create_memory(db, company_id, mode, company_name)

    # Optimistic concurrency check
    if payload.version != row.version:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version_conflict",
                "message": "Another user has modified this memory. Please reload and try again.",
                "server_version": row.version,
            },
        )

    # Save current content as a version snapshot before overwriting
    _save_version_snapshot(db, row, saved_by=user_id, saved_by_type="user")

    row.content = payload.content
    row.version = row.version + 1
    row.updated_by_user = user_id
    row.updated_by_type = "user"
    db.commit()

    return {
        "status": "ok",
        "mode": mode,
        "version": row.version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/company/memory/{mode}/history", response_model=list[RuleMemoryVersionItem])
async def get_rule_memory_history(
    mode: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    mode = normalize_processing_mode(mode)
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Valid: {sorted(VALID_MODES)}")

    company_name = _get_company_name(db, company_id)
    row = _get_or_create_memory(db, company_id, mode, company_name)
    versions = (
        db.query(CompanyRuleMemoryVersion)
        .filter(CompanyRuleMemoryVersion.memory_id == row.id)
        .order_by(CompanyRuleMemoryVersion.version.desc())
        .limit(MAX_VERSIONS)
        .all()
    )
    return [
        {
            "version": v.version,
            "saved_at": v.saved_at.isoformat() if v.saved_at else None,
            "saved_by": v.saved_by,
            "saved_by_type": v.saved_by_type,
            "content_preview": v.content[:200] + "..." if len(v.content) > 200 else v.content,
        }
        for v in versions
    ]


@router.post("/company/memory/{mode}/restore/{version_num}", response_model=RuleMemoryRestoreResponse)
async def restore_rule_memory_version(
    mode: str,
    version_num: int,
    company_id: str = Depends(get_current_company_id),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    mode = normalize_processing_mode(mode)
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Valid: {sorted(VALID_MODES)}")

    company_name = _get_company_name(db, company_id)
    row = _get_or_create_memory(db, company_id, mode, company_name)
    snap = (
        db.query(CompanyRuleMemoryVersion)
        .filter(
            CompanyRuleMemoryVersion.memory_id == row.id,
            CompanyRuleMemoryVersion.version == version_num,
        )
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Version {version_num} not found")

    # Save current as snapshot before restoring
    _save_version_snapshot(db, row, saved_by=user_id, saved_by_type="user")

    row.content = snap.content
    row.version = row.version + 1
    row.updated_by_user = user_id
    row.updated_by_type = "user"
    db.commit()

    return {
        "status": "ok",
        "restored_from_version": version_num,
        "new_version": row.version,
    }


@router.get("/company/memory/export")
async def export_rule_memories(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Export all VALID_MODES rule-memory MD files as a zip."""
    company_name = _get_company_name(db, company_id)
    result = {}
    for mode in sorted(VALID_MODES):
        row = _get_or_create_memory(db, company_id, mode, company_name)
        result[mode] = {
            "content": row.content,
            "version": row.version,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    # Build zip in memory
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for mode, data in result.items():
            zf.writestr(f"{mode.lower()}_rules.md", data["content"])
        zf.writestr(
            "export_manifest.json",
            json.dumps({
                "company_id": company_id,
                "exported_at": datetime.utcnow().isoformat(),
                "modes": {m: {"version": d["version"], "updated_at": d["updated_at"]} for m, d in result.items()},
            }, indent=2),
        )
    buf.seek(0)
    filename = f"rule_memory_{company_id}_{datetime.utcnow().strftime('%Y%m%d')}.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/company/memory/import", response_model=RuleMemoryImportResponse)
async def import_rule_memories(
    payload: RuleMemoryImportRequest,
    company_id: str = Depends(get_current_company_id),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    company_name = _get_company_name(db, company_id)
    imported = []
    for item in payload.memories:
        mode = normalize_processing_mode(str(item.get("mode", "")))
        content = str(item.get("content", "")).strip()
        if mode not in VALID_MODES or not content:
            continue
        row = _get_or_create_memory(db, company_id, mode, company_name)
        _save_version_snapshot(db, row, saved_by=user_id, saved_by_type="import")
        row.content = content
        row.version = row.version + 1
        row.updated_by_user = user_id
        row.updated_by_type = "import"
        imported.append(mode)
    db.commit()
    return {"status": "ok", "imported_modes": imported}


@router.post("/company/memory/{mode}/generate", response_model=RuleMemoryGenerateResponse)
async def generate_rule_memory(
    mode: str,
    payload: RuleMemoryGenerateRequest,
    company_id: str = Depends(get_current_company_id),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """
    AI generates starter rules from the user's business description.
    Merges with the HK base template and saves to DB.
    """
    # Rate limit + cost cap
    rate_ok, rate_msg = await check_generation_rate_async(company_id)
    if not rate_ok:
        raise HTTPException(status_code=429, detail=rate_msg)
    cost_ok, cost_msg = check_monthly_cost(db, company_id)
    if not cost_ok:
        raise HTTPException(status_code=429, detail=cost_msg)

    mode = normalize_processing_mode(mode)
    if mode not in VALID_MODES:
        raise HTTPException(status_code=400, detail=f"Invalid mode. Valid: {sorted(VALID_MODES)}")

    # Sanitise and cap description input
    if payload.business_description:
        payload = payload.model_copy(
            update={"business_description": normalise_input(payload.business_description, max_chars=_MAX_GEN_CHARS)}
        )

    company_name = payload.company_name or _get_company_name(db, company_id)
    base_template = get_starter_template(mode, company_name)

    # Call LLM to generate custom rules based on business description
    llm_reply = ""
    if payload.business_description and _DEPLOY_API_KEY:
        try:
            prompt = (
                f"A company is setting up their {mode} rule memory. "
                f"Business description: {payload.business_description}\n\n"
                f"Generate additional rule lines (in Markdown bullet format) that should be "
                f"added to the {mode} rules memory for this company. "
                f"Only output bullet points in this exact format:\n"
                f'- "keyword1", "keyword2" → Account: XXXX\n'
                f"- Vendor Name → Account: XXXX, Tax: ST\n\n"
                f"Do not output section headers or explanations. Only output rule lines."
            )
            system_msg = build_hardened_system_prompt(
                "You are a Hong Kong accounting rules assistant. "
                "You ONLY output accounting rule lines in the specified Markdown format. "
                "You never output anything else."
            )
            resp = requests.post(
                openai_chat_completions_url(_DEPLOY_BASE_URL),
                headers={
                    "Authorization": f"Bearer {_DEPLOY_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": settings.deploy_model,
                    "messages": [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 512,
                    "temperature": 0.2,
                },
                timeout=(10, 60),
                verify=True,
            )
            if resp.status_code == 200:
                raw_llm = resp.json()["choices"][0]["message"]["content"]
                _safe, raw_llm = scan_output(raw_llm, company_id)
                if _safe:
                    llm_reply = raw_llm
        except Exception:
            pass  # Fall back to base template only

    final_content = get_ai_generated_template(mode, company_name, payload.business_description, llm_reply)

    row = _get_or_create_memory(db, company_id, mode, company_name)
    if row.version > 1:
        # Only save snapshot if there's existing content worth preserving
        _save_version_snapshot(db, row, saved_by=user_id, saved_by_type="user")

    row.content = final_content
    row.version = row.version + 1
    row.updated_by_user = user_id
    row.updated_by_type = "ai"
    db.commit()

    return {
        "status": "ok",
        "mode": mode,
        "version": row.version,
        "content": final_content,
    }

import os
import uuid
from typing import Any, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id
from app.core.config import settings
from app.core.text_limits import (
    MAX_COMPANY_KEYWORD_ITEM_CHARS,
    MAX_COMPANY_KEYWORDS_COUNT,
    MAX_COMPANY_NAME_CHARS,
    MAX_MARKDOWN_BODY_CHARS,
    MAX_PROFILE_SHORT_FIELD_CHARS,
)
from app.database import get_db
from app.models.company_context import CompanyProfile

router = APIRouter()

_DEPLOY_API_KEY  = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
_DEPLOY_BASE_URL = (
    os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or "https://www.dmxapi.cn"
).rstrip("/")


class CompanyProfileRequest(BaseModel):
    industry: Optional[str] = Field(default=None, max_length=MAX_PROFILE_SHORT_FIELD_CHARS)
    accounting_basis: Optional[str] = Field(default=None, max_length=MAX_PROFILE_SHORT_FIELD_CHARS)
    fiscal_year_end: Optional[str] = Field(default=None, max_length=MAX_PROFILE_SHORT_FIELD_CHARS)
    company_name: Optional[str] = Field(default=None, max_length=MAX_COMPANY_NAME_CHARS)
    company_name_keywords: list[str] | None = None
    custom_settings: dict[str, Any] | None = None

    @field_validator("company_name_keywords")
    @classmethod
    def _keywords_bounds(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        if len(v) > MAX_COMPANY_KEYWORDS_COUNT:
            raise ValueError(f"at most {MAX_COMPANY_KEYWORDS_COUNT} keywords allowed")
        for item in v:
            if len(item) > MAX_COMPANY_KEYWORD_ITEM_CHARS:
                raise ValueError("keyword entry too long")
        return v


class CompanyProfileResponse(BaseModel):
    company_id: str
    industry: Optional[str]
    accounting_basis: Optional[str]
    fiscal_year_end: Optional[str]
    company_name: Optional[str]
    company_name_keywords: list[str]
    custom_settings: dict[str, Any]
    exists: bool


class CompanyProfileSaveResponse(BaseModel):
    status: str
    company_id: str


@router.get("/company/profile", response_model=CompanyProfileResponse)
async def get_company_profile(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    if profile is None:
        return {
            "company_id": company_id,
            "industry": None,
            "accounting_basis": None,
            "fiscal_year_end": None,
            "company_name": None,
            "company_name_keywords": [],
            "custom_settings": {},
            "exists": False,
        }
    profile_settings = profile.custom_settings if isinstance(profile.custom_settings, dict) else {}
    company_name = (
        profile.company_name
        or (profile_settings.get("company_name") if isinstance(profile_settings.get("company_name"), str) else None)
    )
    company_name_keywords = (
        profile.company_name_keywords
        if isinstance(profile.company_name_keywords, list)
        else (
            profile_settings.get("company_name_keywords")
            if isinstance(profile_settings.get("company_name_keywords"), list)
            else []
        )
    )
    custom_settings = profile.custom_settings if isinstance(profile.custom_settings, dict) else {}
    return {
        "company_id": company_id,
        "industry": profile.industry,
        "accounting_basis": profile.accounting_basis,
        "fiscal_year_end": profile.fiscal_year_end,
        "company_name": company_name,
        "company_name_keywords": company_name_keywords,
        "custom_settings": custom_settings,
        "exists": True,
    }


@router.post("/company/profile", response_model=CompanyProfileSaveResponse)
async def upsert_company_profile(
    payload: CompanyProfileRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    if profile is None:
        profile = CompanyProfile(
            id=str(uuid.uuid4()),
            company_id=company_id,
        )
        db.add(profile)

    profile.industry = payload.industry
    profile.accounting_basis = payload.accounting_basis
    profile.fiscal_year_end = payload.fiscal_year_end
    profile.company_name = (payload.company_name or "").strip() or None
    keywords = payload.company_name_keywords or []
    sanitized_keywords: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        normalized = str(keyword or "").strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        sanitized_keywords.append(normalized)
        if len(sanitized_keywords) >= 30:
            break
    profile.company_name_keywords = sanitized_keywords
    existing_settings = profile.custom_settings if isinstance(profile.custom_settings, dict) else {}
    incoming_settings = payload.custom_settings if isinstance(payload.custom_settings, dict) else {}
    merged_settings = {
        **existing_settings,
        **incoming_settings,
        "company_name": profile.company_name,
        "company_name_keywords": sanitized_keywords,
    }
    profile.custom_settings = merged_settings

    db.commit()
    return {"status": "ok", "company_id": company_id}


# ── Company Profile MD endpoints ──────────────────────────────────────────────

def _build_profile_md(profile: CompanyProfile, company_id: str) -> str:
    """Auto-generate a Profile MD from the structured DB fields."""
    name = profile.company_name or company_id
    industry = profile.industry or "Not specified"
    basis = profile.accounting_basis or "Not specified"
    fy = profile.fiscal_year_end or "Not specified"
    custom = profile.custom_settings if isinstance(profile.custom_settings, dict) else {}
    currency = custom.get("currency", "HKD")
    business_desc = custom.get("business_description", "")
    bank_names = custom.get("bank_names", "")

    lines = [
        "# Company Profile",
        "",
        "## Company Identity",
        f"- Legal / Trading Name: {name}",
        f"- Company ID: {company_id}",
        "",
        "## Business Nature",
        f"- Industry: {industry}",
    ]
    if business_desc:
        lines.append(f"- Business Description: {business_desc}")
    lines += [
        "",
        "## Accounting Settings",
        f"- Accounting Basis: {basis}",
        f"- Fiscal Year End: {fy}",
        f"- Reporting Currency: {currency}",
        "",
        "## Bank Accounts",
    ]
    if bank_names:
        for b in [b.strip() for b in bank_names.split(",") if b.strip()]:
            lines.append(f"- {b}")
    else:
        lines.append("- *(Add your company's bank names here, e.g. HSBC, BOC)*")
    lines += [
        "",
        "## Other Summary",
        "- *(Key loans and fixed assets — update via AI chat or manually)*",
        "- *(Detailed entries are managed in OTHER Rules Memory)*",
        "",
        "## Special Notes",
        "- *(Add any special accounting notes or instructions here)*",
    ]
    return "\n".join(lines)


class ProfileMdRequest(BaseModel):
    profile_md: str = Field(..., max_length=MAX_MARKDOWN_BODY_CHARS)


@router.get("/company/profile-md")
async def get_profile_md(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    if profile is None:
        return {"profile_md": "", "exists": False}
    return {
        "profile_md": profile.profile_md or "",
        "exists": bool(profile.profile_md),
    }


@router.put("/company/profile-md")
async def save_profile_md(
    payload: ProfileMdRequest,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    if profile is None:
        profile = CompanyProfile(id=str(uuid.uuid4()), company_id=company_id)
        db.add(profile)
    profile.profile_md = payload.profile_md.strip()
    db.commit()
    return {"status": "ok"}


@router.post("/company/profile-md/generate")
async def generate_profile_md(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """
    Auto-generate Profile MD from existing DB fields.
    If an AI key is configured, ask the LLM to flesh out the template;
    otherwise return the structured template directly.
    """
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    if profile is None:
        profile = CompanyProfile(id=str(uuid.uuid4()), company_id=company_id)
        db.add(profile)

    base_md = _build_profile_md(profile, company_id)

    # If no AI key configured, just save and return the template
    if not _DEPLOY_API_KEY:
        profile.profile_md = base_md
        db.commit()
        return {"status": "ok", "profile_md": base_md, "source": "template"}

    # Ask AI to enrich the template based on what it knows
    try:
        resp = requests.post(
            f"{_DEPLOY_BASE_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {_DEPLOY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.deploy_model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are a Hong Kong accounting assistant. "
                            "Your task is to review and lightly improve a company profile Markdown document. "
                            "Keep all section headers exactly as-is. "
                            "Fill in any placeholders where you have information. "
                            "Do not invent facts — leave placeholders if unsure. "
                            "Return ONLY the improved Markdown, no commentary."
                        ),
                    },
                    {
                        "role": "user",
                        "content": f"Please review and improve this company profile:\n\n{base_md}",
                    },
                ],
                "max_tokens": 800,
                "temperature": 0.2,
            },
            timeout=(15, 60),
            verify=True,
        )
        resp.raise_for_status()
        ai_md = resp.json()["choices"][0]["message"]["content"].strip()
        if len(ai_md) > 200:
            base_md = ai_md
    except Exception as exc:
        # AI enrichment failed — fall back to template silently
        import logging
        logging.getLogger(__name__).warning("[ProfileMD] AI enrichment failed: %s", exc)

    profile.profile_md = base_md
    db.commit()
    return {"status": "ok", "profile_md": base_md, "source": "ai_generated"}

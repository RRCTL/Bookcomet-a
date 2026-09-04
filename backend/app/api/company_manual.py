"""
Company Manual API
==================
GET  /company/manual          — get manual content + version
PUT  /company/manual          — save updated manual (with version check)
GET  /company/manual/history  — last 5 version snapshots
POST /company/manual/restore/{version_num} — restore a past version
GET  /company/manual/exists   — check if a non-empty manual exists (for wizard trigger)
POST /company/manual/generate — AI generates manual from wizard answers

Security: company_id always comes from the JWT/header dependency, never from request body.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from typing import List, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user_id
from app.core.gateway_settings import openai_chat_completions_url
from app.core.config import settings
from app.core.text_limits import (
    MAX_BANK_FIELD_CHARS,
    MAX_MARKDOWN_BODY_CHARS,
    MAX_WIZARD_SECTION_CHARS,
    MAX_WIZARD_SHORT_FIELD_CHARS,
)
from app.database import get_db
from app.models.company_manual import CompanyManual, CompanyManualVersion, MAX_MANUAL_VERSIONS
from app.models.company_context import CompanyProfile, CompanyRule
from app.services.rule_governance import (
    RULE_TYPE_COMPANY_CONTEXT,
    validate_company_context_payload,
)

KNOWLEDGE_CONTEXT_RULE_NAME = "Business context"

logger = logging.getLogger(__name__)

router = APIRouter()

_DEPLOY_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
_DEPLOY_BASE_URL = (
    os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or os.getenv("VLM_BASE_URL") or ""
).rstrip("/")


# ── Request / Response models ─────────────────────────────────────────────────

class ManualSaveRequest(BaseModel):
    content: str = Field(..., max_length=MAX_MARKDOWN_BODY_CHARS)
    version: int


class BankAccountInput(BaseModel):
    bank_name: Optional[str] = Field(default=None, max_length=MAX_BANK_FIELD_CHARS)
    account_nickname: Optional[str] = Field(default=None, max_length=MAX_BANK_FIELD_CHARS)
    currency: str = Field(default="HKD", max_length=16)
    opening_balance: Optional[str] = Field(default=None, max_length=MAX_BANK_FIELD_CHARS)
    dr_cr: str = Field(default="Dr", max_length=8)


class BankSettingsInput(BaseModel):
    payment_method: str = Field(default="bank", max_length=32)  # bank | cash | both
    accounts: List[BankAccountInput] = Field(default_factory=list)
    cash_account: Optional[BankAccountInput] = None
    director_account: Optional[BankAccountInput] = None

    @field_validator("accounts")
    @classmethod
    def _accounts_cap(cls, v: List[BankAccountInput]) -> List[BankAccountInput]:
        if len(v) > 80:
            raise ValueError("at most 80 bank accounts allowed")
        return v


class WizardAnswers(BaseModel):
    company_name: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SHORT_FIELD_CHARS)
    company_name_keywords: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SHORT_FIELD_CHARS)
    industry: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SHORT_FIELD_CHARS)
    accounting_basis: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SHORT_FIELD_CHARS)
    fiscal_year_end: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SHORT_FIELD_CHARS)
    key_clients: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SECTION_CHARS)
    key_vendors: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SECTION_CHARS)
    risk_rules: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SECTION_CHARS)
    glossary: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SECTION_CHARS)
    generate_rule_memory: bool = True
    generate_coa: bool = True
    business_description: Optional[str] = Field(default=None, max_length=MAX_WIZARD_SECTION_CHARS)
    bank_settings: Optional[BankSettingsInput] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_or_create_manual(db: Session, company_id: str) -> CompanyManual:
    row = db.query(CompanyManual).filter(CompanyManual.company_id == company_id).first()
    if row is None:
        row = CompanyManual(
            id=str(uuid.uuid4()),
            company_id=company_id,
            content="",
            version=1,
            updated_by_type="system",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _save_version_snapshot(
    db: Session,
    manual: CompanyManual,
    saved_by: str | None,
    saved_by_type: str,
) -> None:
    snap = CompanyManualVersion(
        id=str(uuid.uuid4()),
        manual_id=manual.id,
        company_id=manual.company_id,
        version=manual.version,
        content=manual.content,
        saved_by=saved_by,
        saved_by_type=saved_by_type,
    )
    db.add(snap)
    db.flush()

    all_versions = (
        db.query(CompanyManualVersion)
        .filter(CompanyManualVersion.manual_id == manual.id)
        .order_by(CompanyManualVersion.version.desc())
        .all()
    )
    if len(all_versions) > MAX_MANUAL_VERSIONS:
        for old in all_versions[MAX_MANUAL_VERSIONS:]:
            db.delete(old)


def _build_manual_from_wizard(answers: WizardAnswers, company_name: str) -> str:
    """Compose the Company Manual Markdown from wizard answers."""
    lines = [f"# Company Manual — {company_name or 'My Company'}", ""]

    def _section(title: str, content: str | None) -> list[str]:
        if not content or not content.strip():
            return [f"## {title}", "", "*(No information provided yet.)*", ""]
        return [f"## {title}", "", content.strip(), ""]

    lines += _section("Key Clients", answers.key_clients)
    lines += _section("Key Vendors", answers.key_vendors)
    lines += _section("Risk & Compliance Rules", answers.risk_rules)
    lines += _section("Company Glossary", answers.glossary)

    # Bank settings section
    bs = answers.bank_settings
    if bs:
        bank_lines = ["## Bank & Cash Accounts", ""]

        def _bal_str(acct: BankAccountInput) -> str:
            if acct.opening_balance and str(acct.opening_balance).strip():
                return f", Opening balance: {acct.currency} {acct.opening_balance} {acct.dr_cr}"
            return ""

        if bs.payment_method in ("bank", "both"):
            for i, acct in enumerate(bs.accounts):
                name = acct.account_nickname or acct.bank_name or f"Bank Account {i+1}"
                bank_lines.append(f"- 🏦 {name} ({acct.bank_name or 'Bank'}, {acct.currency}){_bal_str(acct)}")

        if bs.payment_method in ("cash", "both") and bs.cash_account:
            ca = bs.cash_account
            name = ca.account_nickname or "Cash on Hand"
            bank_lines.append(f"- 💵 {name} ({ca.currency}){_bal_str(ca)}")

        if bs.payment_method == "cash" and not bs.accounts:
            bank_lines.append("- Cash-only operation. No bank accounts.")

        if bs.director_account:
            da = bs.director_account
            name = da.account_nickname or "Director's Current Account"
            bank_lines.append(f"- 👤 {name} (suspense account){_bal_str(da)}")

        bank_lines.append("")
        lines += bank_lines

    return "\n".join(lines)


def _coalesce_text(left: str, right: str) -> str:
    """Keep both existing knowledge sources when they differ; prefer the richer copy."""
    left = (left or "").strip()
    right = (right or "").strip()
    if not left:
        return right
    if not right:
        return left
    if left == right or right in left:
        return left
    if left in right:
        return right
    return f"{left}\n\n---\n\n{right}"


def _merge_manual_content(existing: str, generated: str) -> str:
    """Combine wizard output with knowledge that already exists. Never drop existing text."""
    existing = (existing or "").strip()
    generated = (generated or "").strip()
    if not existing:
        return generated
    if not generated:
        return existing
    if generated in existing:
        return existing
    if existing in generated:
        return generated
    return f"{existing}\n\n---\n\n# Setup wizard update\n\n{generated}"


def _knowledge_context_body(db: Session, company_id: str) -> str:
    row = (
        db.query(CompanyRule)
        .filter(
            CompanyRule.company_id == company_id,
            CompanyRule.rule_type == RULE_TYPE_COMPANY_CONTEXT,
        )
        .first()
    )
    if not row or not isinstance(row.rule_json, dict):
        return ""
    return str(row.rule_json.get("body") or "").strip()


def _existing_knowledge_text(db: Session, company_id: str) -> str:
    manual = db.query(CompanyManual).filter(CompanyManual.company_id == company_id).first()
    manual_text = (manual.content or "").strip() if manual else ""
    return _coalesce_text(_knowledge_context_body(db, company_id), manual_text)


def _is_wizard_completed(profile: CompanyProfile | None) -> bool:
    if not profile or not isinstance(profile.custom_settings, dict):
        return False
    return profile.custom_settings.get("wizard_completed") is True


def _mark_wizard_completed(profile: CompanyProfile) -> None:
    current = profile.custom_settings if isinstance(profile.custom_settings, dict) else {}
    profile.custom_settings = {**current, "wizard_completed": True}


_CJK_RE = re.compile(r"[\u3400-\u9fff]")


def _wizard_user_text(answers: WizardAnswers, existing_md: str = "") -> str:
    """Concatenate user-entered wizard fields and existing knowledge for language detection."""
    parts = [
        answers.company_name,
        answers.company_name_keywords,
        answers.industry,
        answers.business_description,
        answers.key_clients,
        answers.key_vendors,
        answers.risk_rules,
        answers.glossary,
    ]
    bs = answers.bank_settings
    if bs:
        for acct in bs.accounts:
            parts.extend([acct.bank_name, acct.account_nickname])
        if bs.cash_account:
            parts.extend([bs.cash_account.bank_name, bs.cash_account.account_nickname])
        if bs.director_account:
            parts.extend([bs.director_account.bank_name, bs.director_account.account_nickname])
    parts.append(existing_md)
    return "\n".join(str(p) for p in parts if p and str(p).strip())


def _wizard_content_language_instruction(answers: WizardAnswers, existing_md: str = "") -> str:
    """Keep wizard chrome English; follow the language the user typed in the fields."""
    if _CJK_RE.search(_wizard_user_text(answers, existing_md)):
        return (
            "The user typed Chinese in the wizard fields. Keep their Chinese text exactly. "
            "Write any added guidance in the same Chinese the user used. "
            "Do not translate their answers into English. "
            "Keep Markdown section headers in English."
        )
    return (
        "The user wrote in English. Write added guidance in English. "
        "Do not add Chinese unless the user typed it. "
        "Keep Markdown section headers in English."
    )


def _ai_enhance_manual(raw_md: str, answers: WizardAnswers, existing_md: str = "") -> str:
    """
    Optionally call the LLM to enrich and structure the manual from wizard free-text.
    Returns original raw_md if LLM call fails or API key is absent.
    """
    if not _DEPLOY_API_KEY:
        return raw_md

    desc_parts = []
    if answers.industry:
        desc_parts.append(f"Industry: {answers.industry}")
    if answers.accounting_basis:
        desc_parts.append(f"Accounting basis: {answers.accounting_basis}")
    if answers.business_description:
        desc_parts.append(f"Business: {answers.business_description}")

    existing_block = ""
    if (existing_md or "").strip():
        existing_block = (
            "The company already has knowledge. Keep all of it and combine it with "
            "the new wizard draft. Do not drop existing facts.\n\n"
            f"Existing knowledge:\n{existing_md.strip()}\n\n"
        )

    prompt = (
        "You are a Hong Kong accounting assistant. A user has provided initial information "
        "about their company. Enhance and structure the following Company Manual in Markdown. "
        "Keep all provided information. Add practical accounting guidance relevant to Hong Kong SMEs. "
        f"{_wizard_content_language_instruction(answers, existing_md)}\n\n"
        f"Company context: {'; '.join(desc_parts)}\n\n"
        f"{existing_block}"
        f"Draft manual:\n{raw_md}\n\n"
        "Output ONLY the improved Markdown manual. Do not add explanations."
    )
    try:
        resp = requests.post(
            openai_chat_completions_url(_DEPLOY_BASE_URL),
            headers={
                "Authorization": f"Bearer {_DEPLOY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.deploy_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1500,
                "temperature": 0.3,
            },
            timeout=(10, 90),
            verify=True,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        pass
    return raw_md


def _parse_balance(raw: str | None) -> float | None:
    if not raw or not str(raw).strip():
        return None
    try:
        return float(raw)
    except (ValueError, TypeError):
        return None


def _profile_header_md(profile: CompanyProfile | None) -> str:
    if not profile:
        return ""
    lines: list[str] = []
    if profile.company_name:
        lines.append(f"- **Company:** {profile.company_name}")
    if profile.industry:
        lines.append(f"- **Industry:** {profile.industry}")
    if profile.accounting_basis:
        lines.append(f"- **Accounting basis:** {profile.accounting_basis}")
    if profile.fiscal_year_end:
        lines.append(f"- **Fiscal year end:** {profile.fiscal_year_end}")
    kw = profile.company_name_keywords
    if kw and isinstance(kw, list) and kw:
        lines.append(f"- **Name keywords:** {', '.join(str(x) for x in kw)}")
    if not lines:
        return ""
    return "## Profile\n" + "\n".join(lines)


def _upsert_knowledge_context_from_wizard(
    db: Session,
    company_id: str,
    user_id: str,
    profile: CompanyProfile | None,
    manual_md: str,
) -> None:
    """Create or update the singleton Knowledge Business context from wizard output."""
    header = _profile_header_md(profile)
    md = (manual_md or "").strip()
    parts = [p for p in (header, md) if p]
    body = "\n\n".join(parts).strip()
    if not body:
        return
    validate_company_context_payload(body=body, use_when=None)

    row = (
        db.query(CompanyRule)
        .filter(
            CompanyRule.company_id == company_id,
            CompanyRule.rule_type == RULE_TYPE_COMPANY_CONTEXT,
        )
        .first()
    )
    if row is None:
        db.add(
            CompanyRule(
                id=str(uuid.uuid4()),
                company_id=company_id,
                rule_name=KNOWLEDGE_CONTEXT_RULE_NAME,
                rule_type=RULE_TYPE_COMPANY_CONTEXT,
                vendor_pattern=None,
                keyword_pattern=None,
                amount_pattern=None,
                document_type=None,
                rule_json={"use_when": None, "body": body},
                priority=0,
                is_active=True,
                notes=None,
                created_by=user_id,
            )
        )
    else:
        row.rule_json = {"use_when": None, "body": body}


def _coa_name_exists(db: Session, company_id: str, name: str) -> bool:
    from app.models.reconciliation import ChartOfAccountEntry
    return db.query(ChartOfAccountEntry).filter(
        ChartOfAccountEntry.company_id == company_id,
        ChartOfAccountEntry.name_en == name,
    ).first() is not None


def _coa_code_exists(db: Session, company_id: str, code: str) -> bool:
    from app.models.reconciliation import ChartOfAccountEntry
    return db.query(ChartOfAccountEntry).filter(
        ChartOfAccountEntry.company_id == company_id,
        ChartOfAccountEntry.code == code,
    ).first() is not None


def _add_entry(
    db: Session,
    company_id: str,
    code: str,
    name_en: str,
    category_type: str,
    allowed_modes: list,
    opening_balance: float | None,
    dr_cr: str | None,
) -> None:
    from app.models.reconciliation import ChartOfAccountEntry
    entry = ChartOfAccountEntry(
        id=str(uuid.uuid4()),
        company_id=company_id,
        code=code,
        name_en=name_en,
        name_zh="",
        category_type=category_type,
        allowed_modes=allowed_modes,
        is_default=False,
        opening_balance=opening_balance,
        opening_balance_dr_cr=dr_cr if opening_balance is not None else None,
    )
    db.add(entry)


def _create_bank_coa_entries(
    db: Session,
    company_id: str,
    bank_settings: BankSettingsInput,
) -> list[str]:
    """
    Create ChartOfAccountEntry rows for:
    - Bank accounts       → 1100–1199 (asset)
    - Cash on Hand        → 1010 (asset)
    - Director's Account  → 2100 (liability)
    Skips if entry with the same name already exists.
    Returns list of created account codes.
    """
    created: list[str] = []
    pm = bank_settings.payment_method

    # ── 1. Bank accounts (1100–1199) ──────────────────────────────────────────
    if pm in ("bank", "both"):
        used_bank_codes: set[int] = set()
        from app.models.reconciliation import ChartOfAccountEntry
        rows = db.query(ChartOfAccountEntry.code).filter(
            ChartOfAccountEntry.company_id == company_id,
        ).all()
        for (code,) in rows:
            try:
                n = int(code)
                if 1100 <= n <= 1199:
                    used_bank_codes.add(n)
            except (ValueError, TypeError):
                pass

        next_code = 1100
        for acct in bank_settings.accounts:
            name = (acct.account_nickname or acct.bank_name or "").strip()
            if not name:
                name = f"Bank Account {next_code}"

            if _coa_name_exists(db, company_id, name):
                continue

            while next_code in used_bank_codes and next_code <= 1199:
                next_code += 1
            if next_code > 1199:
                break

            _add_entry(
                db, company_id, str(next_code), name,
                "asset", ["BANK"],
                _parse_balance(acct.opening_balance), acct.dr_cr,
            )
            used_bank_codes.add(next_code)
            created.append(str(next_code))
            next_code += 1

    # ── 2. Cash on Hand (1010) ────────────────────────────────────────────────
    if pm in ("cash", "both") and bank_settings.cash_account:
        ca = bank_settings.cash_account
        name = (ca.account_nickname or "Cash on Hand").strip()
        if not _coa_code_exists(db, company_id, "1010") and not _coa_name_exists(db, company_id, name):
            _add_entry(
                db, company_id, "1010", name,
                "asset", ["BANK", "AR", "AP"],
                _parse_balance(ca.opening_balance), ca.dr_cr,
            )
            created.append("1010")

    # ── 3. Director / Owner account (2100) ────────────────────────────────────
    if bank_settings.director_account:
        da = bank_settings.director_account
        name = (da.account_nickname or "Director's Current Account").strip()
        if not _coa_code_exists(db, company_id, "2100") and not _coa_name_exists(db, company_id, name):
            _add_entry(
                db, company_id, "2100", name,
                "liability", ["AR", "AP", "BANK"],
                _parse_balance(da.opening_balance), da.dr_cr,
            )
            created.append("2100")

    return created


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/company/manual")
async def get_company_manual(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Get the company manual content and metadata."""
    row = _get_or_create_manual(db, company_id)
    return {
        "company_id": company_id,
        "content": row.content,
        "version": row.version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by_type": row.updated_by_type,
    }


@router.get("/company/manual/exists")
async def manual_exists(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Return whether this workspace has company knowledge and whether its wizard finished."""
    row = db.query(CompanyManual).filter(CompanyManual.company_id == company_id).first()
    has_manual = bool(row and row.content and row.content.strip())
    has_knowledge = bool(_knowledge_context_body(db, company_id))
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    return {
        "exists": has_manual or has_knowledge,
        "wizard_completed": _is_wizard_completed(profile),
    }


@router.put("/company/manual")
async def save_company_manual(
    payload: ManualSaveRequest,
    company_id: str = Depends(get_current_company_id),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Save updated manual content with optimistic concurrency check."""
    row = _get_or_create_manual(db, company_id)

    if payload.version != row.version:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "version_conflict",
                "message": "Manual was modified elsewhere. Please reload and try again.",
                "server_version": row.version,
            },
        )

    if row.content.strip():
        _save_version_snapshot(db, row, saved_by=user_id, saved_by_type="user")

    row.content = payload.content
    row.version = row.version + 1
    row.updated_by_user = user_id
    row.updated_by_type = "user"
    db.commit()

    return {
        "status": "ok",
        "version": row.version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


@router.get("/company/manual/history")
async def get_manual_history(
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    """Return last N version snapshots."""
    row = _get_or_create_manual(db, company_id)
    versions = (
        db.query(CompanyManualVersion)
        .filter(CompanyManualVersion.manual_id == row.id)
        .order_by(CompanyManualVersion.version.desc())
        .limit(MAX_MANUAL_VERSIONS)
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


@router.post("/company/manual/restore/{version_num}")
async def restore_manual_version(
    version_num: int,
    company_id: str = Depends(get_current_company_id),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """Restore a past version snapshot."""
    row = _get_or_create_manual(db, company_id)
    snap = (
        db.query(CompanyManualVersion)
        .filter(
            CompanyManualVersion.manual_id == row.id,
            CompanyManualVersion.version == version_num,
        )
        .first()
    )
    if snap is None:
        raise HTTPException(status_code=404, detail=f"Version {version_num} not found")

    _save_version_snapshot(db, row, saved_by=user_id, saved_by_type="user")

    row.content = snap.content
    row.version = row.version + 1
    row.updated_by_user = user_id
    row.updated_by_type = "user"
    db.commit()

    return {"status": "ok", "restored_from_version": version_num, "new_version": row.version}


@router.post("/company/manual/generate")
async def generate_manual_from_wizard(
    answers: WizardAnswers,
    company_id: str = Depends(get_current_company_id),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    """
    Wizard endpoint: save structured profile fields to CompanyProfile DB,
    then generate and save the Company Manual MD from wizard answers.
    Always generates Rule Memory for all 4 modes and updates CoA.
    """
    _gen_t0 = time.perf_counter()
    logger.info(
        "company_manual.generate start company_id=%s user_id=%s",
        company_id,
        user_id,
    )
    # 1. Save structured fields to CompanyProfile (used by code, not shown in UI)
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    if profile is None:
        profile = CompanyProfile(id=str(uuid.uuid4()), company_id=company_id)
        db.add(profile)

    if answers.company_name is not None:
        profile.company_name = answers.company_name
    if answers.industry is not None:
        profile.industry = answers.industry
    if answers.accounting_basis is not None:
        profile.accounting_basis = answers.accounting_basis
    if answers.fiscal_year_end is not None:
        profile.fiscal_year_end = answers.fiscal_year_end
    if answers.company_name_keywords is not None:
        kws = [k.strip() for k in answers.company_name_keywords.split(",") if k.strip()]
        profile.company_name_keywords = kws

    db.flush()

    # 2. Build manual MD from wizard answers and combine with existing knowledge
    company_name = answers.company_name or (profile.company_name or "My Company")
    raw_md = _build_manual_from_wizard(answers, company_name)
    existing_md = _existing_knowledge_text(db, company_id)

    # 3. AI-enhance the manual (blocking HTTP — run off the event loop)
    enhanced_md = await asyncio.to_thread(_ai_enhance_manual, raw_md, answers, existing_md)
    merged_md = _merge_manual_content(existing_md, enhanced_md)
    logger.info(
        "company_manual.generate ai_enhance_done company_id=%s elapsed_s=%.2f deploy_key=%s merged=%s",
        company_id,
        time.perf_counter() - _gen_t0,
        "yes" if _DEPLOY_API_KEY else "no",
        "yes" if existing_md else "no",
    )

    # 4. Save to CompanyManual and mark this workspace's wizard finished
    row = _get_or_create_manual(db, company_id)
    if row.content.strip():
        _save_version_snapshot(db, row, saved_by=user_id, saved_by_type="wizard")

    row.content = merged_md
    row.version = row.version + 1
    row.updated_by_user = user_id
    row.updated_by_type = "wizard"
    _mark_wizard_completed(profile)
    db.commit()

    try:
        db.refresh(profile)
        _upsert_knowledge_context_from_wizard(db, company_id, user_id, profile, merged_md)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "company_manual.generate knowledge_context_upsert_failed company_id=%s",
            company_id,
        )

    # 5. Generate Rule Memory for all modes (always)
    rule_memory_results: dict = {}
    if answers.business_description:
        try:
            from app.models.rule_memory import CompanyRuleMemory, VALID_MODES
            from app.services.rule_memory_templates import get_ai_generated_template

            for mode in sorted(VALID_MODES):
                existing = db.query(CompanyRuleMemory).filter(
                    CompanyRuleMemory.company_id == company_id,
                    CompanyRuleMemory.mode == mode,
                ).first()
                if existing and existing.version > 1:
                    rule_memory_results[mode] = "skipped_existing"
                    continue

                content = get_ai_generated_template(
                    mode, company_name, answers.business_description, ""
                )
                if existing is None:
                    existing = CompanyRuleMemory(
                        id=str(uuid.uuid4()),
                        company_id=company_id,
                        mode=mode,
                        is_active=True,
                        content=content,
                        version=1,
                        updated_by_type="wizard",
                    )
                    db.add(existing)
                else:
                    existing.content = content
                    existing.version = existing.version + 1
                    existing.updated_by_type = "wizard"
                rule_memory_results[mode] = "generated"
            db.commit()
        except Exception as exc:
            rule_memory_results["error"] = str(exc)

    # 6. Create CoA entries from bank settings (always)
    coa_results: dict = {"bank_accounts_created": [], "error": None}
    try:
        if answers.bank_settings:
            created_codes = _create_bank_coa_entries(db, company_id, answers.bank_settings)
            db.commit()
            coa_results["bank_accounts_created"] = created_codes
    except Exception as exc:
        coa_results["error"] = str(exc)

    _elapsed = time.perf_counter() - _gen_t0
    logger.info(
        "company_manual.generate ok company_id=%s version=%s elapsed_s=%.2f rule_memory_keys=%s coa_error=%s",
        company_id,
        row.version,
        _elapsed,
        list(rule_memory_results.keys()),
        coa_results.get("error"),
    )
    return {
        "status": "ok",
        "version": row.version,
        "content": row.content,
        "rule_memory_results": rule_memory_results,
        "coa_results": coa_results,
    }

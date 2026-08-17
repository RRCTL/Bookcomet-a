"""
AR/AP/BANK AI Chat endpoint.

POST /api/ai-chat

Accepts the user's message together with a snapshot of the current visible
transaction table and Company / CoA context.  The AI may return:
  • A natural-language reply
  • An optional <PATCHES>[…]</PATCHES> block with JSON field edits to apply
    directly to the frontend table.

Memory is maintained per session using Progressive Summarization
(see app/memory/progressive_summarizer.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user
from app.core.config import settings
from app.core.text_limits import (
    MAX_AI_CHAT_CONTEXT_JSON_BYTES,
    MAX_AI_CHAT_MESSAGE_CHARS,
    MAX_AI_CHAT_MODE_CHARS,
    MAX_AI_CHAT_SESSION_ID_CHARS,
    MAX_AI_TITLE_MESSAGE_CONTENT_CHARS,
    MAX_AI_TITLE_MESSAGES,
)
from app.core.db_concurrency import long_running_db_work_slot
from app.database import get_db
from app.memory.progressive_summarizer import (
    SUMMARIZE_CONFIG,
    compress_messages,
    memory_store,
    should_summarize,
)
from app.models.chat import ChatTask, TaskMessage
from app.models.identity import User
from app.models.company_context import CompanyProfile
from app.models.reconciliation import ChartOfAccountEntry
from app.models.rule_memory import CompanyRuleMemory, VALID_MODES as _RULE_MEMORY_VALID_MODES
from app.services.behavior_detector import behavior_detector
from app.services.ai_chat_client import deploy_chat_client
from app.services.company_manual_service import get_manual_for_injection
from app.services.rule_memory_parser import (
    append_vendor_rule,
    append_keyword_rule,
    append_default_rule,
    check_dedup,
    parse_rules,
)
from app.services.rule_memory_templates import get_starter_template
from app.services.token_logger import log_token_usage
from app.services.abuse_guard import (
    check_chat_rate_async,
    check_monthly_cost,
    check_token_spike,
    validate_chat_message,
    scan_output,
    build_hardened_system_prompt,
)
from app.agent.skill_loader import load_skill

logger = logging.getLogger(__name__)
router = APIRouter()


def _coerce_str_id_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str) and val.strip():
        return [val.strip()]
    if isinstance(val, list):
        return [str(x).strip() for x in val if x is not None and str(x).strip()]
    return []


_PROFILE_ACCOUNTING_RE = re.compile(
    r"\bcoa\b|chart\s+of\s+accounts|會計科目|科目表|general\s+ledger|\bgl\b|journal|"
    r"prepaid|account\s+code|傳票|預付|資產負債",
    re.IGNORECASE,
)


def _should_inject_company_profile_md(message: str, mode: str) -> bool:
    """Inject company profile.md for accounting / GL questions, CoA, or general company keywords."""
    m = message or ""
    ml = m.lower()
    if _PROFILE_ACCOUNTING_RE.search(m):
        return True
    _general = (
        "our company", "my company", "我們公司", "我公司",
        "company profile", "business nature", "業務", "行業",
        "who are we", "what do we do", "我們是做",
        "industry", "行業類型", "accounting basis", "fiscal year",
        "bank account", "our bank", "我們的銀行",
    )
    if any(kw in ml for kw in _general):
        return True
    if "科目" in m and len(m) < 5000:
        return True
    return False


def _normalize_coa_row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    code = str(row.get("code") or "").strip()
    if not code:
        return None
    return {
        "code": code,
        "name_en": row.get("name_en"),
        "name_zh": row.get("name_zh"),
        "category_type": row.get("category_type"),
    }


# ── API credentials (reuse "Deploy Codes AI" settings) ───────────────────────

_DEPLOY_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
_DEPLOY_BASE_URL = (
    os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or "https://www.dmxapi.cn"
).rstrip("/")
_DEPLOY_CONNECT_TIMEOUT = float(os.getenv("DEPLOY_API_CONNECT_TIMEOUT", "15"))
_DEPLOY_READ_TIMEOUT = float(os.getenv("DEPLOY_API_READ_TIMEOUT", "300"))

# System prompts: Bookcomet skills under app/agent/bookcomet_skills/<mode>/SKILL.md (load_skill)


# ── Request / Response models ─────────────────────────────────────────────────

class AIChatRequest(BaseModel):
    session_id: str = Field(..., max_length=MAX_AI_CHAT_SESSION_ID_CHARS)  # e.g. "taskId_AR"
    mode: str = Field(default="AR", max_length=MAX_AI_CHAT_MODE_CHARS)  # AR | AP | BANK | OTHER
    message: str = Field(..., max_length=MAX_AI_CHAT_MESSAGE_CHARS)
    context: dict[str, Any] = Field(default_factory=dict)  # { transactions, coa, ... }

    @model_validator(mode="after")
    def _limit_context_json_size(self):
        raw = json.dumps(self.context, ensure_ascii=False, default=str)
        if len(raw.encode("utf-8")) > MAX_AI_CHAT_CONTEXT_JSON_BYTES:
            raise ValueError(
                f"context JSON exceeds maximum size ({MAX_AI_CHAT_CONTEXT_JSON_BYTES} bytes)"
            )
        return self


class TablePatch(BaseModel):
    id_number: str
    field: str
    value: Any


class GlDraftPatchLineItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    line_id: str | None = None
    account_code: str | None = None
    memo: str | None = None
    debit: float | None = None
    credit: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _alias_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("line_id") in (None, "") and data.get("id"):
            data = {**data, "line_id": data.get("id")}
        return data


class ReconActionItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    op: str                          # match | unmatch | ledger_pending | gl_draft_patch
    bank_txn_ids: list[str] = Field(default_factory=list)
    ledger_txn_ids: list[str] = Field(default_factory=list)
    group_id: str | None = None
    journal_id: str | None = None
    voucher_no: str | None = None
    gl_lines: list[GlDraftPatchLineItem] = Field(default_factory=list)
    deleted_line_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _alias_lines(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("gl_lines") and data.get("lines") is not None:
            data = {**data, "gl_lines": data.get("lines")}
        return data


class RedirectTaskItem(BaseModel):
    task_id: str
    title: str = ""
    mode: str = ""                   # AR | AP | BANK
    reason: str = ""
    fields: list[str] = []


class ReconRedirectPayload(BaseModel):
    """Legacy field; kept for API compatibility. Always null in OCR-first builds."""
    gl_display: str | None = None
    reason_zh: str = ""
    reason_en: str = ""


class AIChatResponse(BaseModel):
    reply: str
    table_patches: list[TablePatch] = []
    save_rule_pending: bool = False        # True = show [Save Rule] button in chat
    save_rule_proposal: dict | None = None # proposed rule details for display
    rule_saved: bool = False               # True = rule was just saved to memory
    rule_saved_message: str = ""           # confirmation message to display
    recon_actions: list[ReconActionItem] = []
    redirect_tasks: list[RedirectTaskItem] = []
    recon_redirect: ReconRedirectPayload | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_company_context(db: Session, company_id: str) -> dict:
    """Load company profile from DB. Rules are now handled by Rule Memory (MD files)."""
    profile = (
        db.query(CompanyProfile)
        .filter(CompanyProfile.company_id == company_id)
        .first()
    )
    profile_settings = (
        profile.custom_settings
        if profile and isinstance(profile.custom_settings, dict)
        else {}
    )
    return {
        "company_name": (
            (profile.company_name if profile else None)
            or profile_settings.get("company_name")
        ),
        "industry": profile.industry if profile else None,
        "accounting_basis": profile.accounting_basis if profile else None,
    }


def _load_coa(db: Session, company_id: str, mode: str) -> list[dict]:
    """Load Chart of Accounts entries relevant to the given mode."""
    entries = (
        db.query(ChartOfAccountEntry)
        .filter(ChartOfAccountEntry.company_id == company_id)
        .all()
    )
    result = []
    for e in entries:
        allowed = e.allowed_modes if isinstance(e.allowed_modes, list) else []
        if mode in allowed or not allowed:
            result.append(
                {
                    "code": e.code,
                    "name_en": e.name_en,
                    "name_zh": e.name_zh,
                    "category_type": e.category_type,
                }
            )
    return result


def _load_other_records(db: Session, task_id: str, company_id: str) -> list[dict]:
    """Load Other-module records for an OTHER task."""
    try:
        from app.models.other import OtherRecord
        from app.services.extraction_validation import (
            merge_validation_into_row,
            validate_other_record,
        )

        records = (
            db.query(OtherRecord)
            .filter(
                OtherRecord.task_id == task_id,
                OtherRecord.company_id == company_id,
            )
            .all()
        )
        out: list[dict] = []
        for r in records:
            rec: dict = {
                "id": r.id,
                "record_type": r.record_type,
                **(r.payload_json or {}),
            }
            vr = validate_other_record(rec)
            merge_validation_into_row(rec, vr)
            out.append(rec)
        return out
    except Exception as exc:
        logger.warning("[AiChat] Failed to load other records: %s", exc)
        return []


def _load_rule_memory(db: Session, company_id: str, mode: str) -> "CompanyRuleMemory | None":
    """Load the CompanyRuleMemory row for (company_id, mode)."""
    from app.core.processing_mode import normalize_processing_mode

    m = normalize_processing_mode(mode)
    if m not in _RULE_MEMORY_VALID_MODES:
        return None
    return db.query(CompanyRuleMemory).filter(
        CompanyRuleMemory.company_id == company_id,
        CompanyRuleMemory.mode == m,
    ).first()


def _rule_memory_md_for_prompt(row: "CompanyRuleMemory | None") -> str:
    """MD injected into prompts; empty when skill is disabled."""
    if row is None:
        return ""
    if getattr(row, "is_active", True) is False:
        return ""
    return row.content or ""


def _ensure_rule_memory(db: Session, company_id: str, mode: str) -> "CompanyRuleMemory":
    """Get or create rule memory row."""
    import uuid as _uuid
    m = mode.upper()
    row = _load_rule_memory(db, company_id, m)
    if row is None:
        profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
        cname = (profile.company_name if profile else None) or ""
        content = get_starter_template(m, cname)
        row = CompanyRuleMemory(
            id=str(_uuid.uuid4()),
            company_id=company_id,
            mode=m,
            content=content,
            version=1,
            updated_by_type="system",
            is_active=True,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def _save_rule_to_memory(
    db: Session,
    company_id: str,
    mode: str,
    rule_type: str,         # "vendor" | "keyword" | "default"
    vendor: str | None,
    keywords: list[str] | None,
    field: str,
    value: str,
    user_id: str = "user",
) -> tuple[bool, str]:
    """
    Append a confirmed rule to the MD memory.
    Returns (success, message).
    """
    try:
        row = _ensure_rule_memory(db, company_id, mode)
        md = row.content or ""

        # Check dedup
        if check_dedup(md, vendor, field, value):
            return True, "rule_already_exists"

        if rule_type == "vendor" and vendor:
            md = append_vendor_rule(md, vendor, field, value)
        elif rule_type == "keyword" and keywords:
            md = append_keyword_rule(md, keywords, field, value)
        else:
            md = append_default_rule(md, field, value)

        row.content = md
        row.version = (row.version or 1) + 1
        row.updated_by_user = user_id
        row.updated_by_type = "ai"
        db.commit()
        return True, "saved"
    except Exception as exc:
        logger.error("[AiChat] Failed to save rule to memory: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass
        return False, str(exc)


def _validate_account_code(db: Session, company_id: str, mode: str, account_code: str) -> dict:
    """
    Check if account_code exists in ChartOfAccountEntry for this company.
    Returns {"exists": bool, "suggestions": list[dict]}.
    """
    if not account_code or not account_code.strip():
        return {"exists": False, "suggestions": []}

    entries = db.query(ChartOfAccountEntry).filter(
        ChartOfAccountEntry.company_id == company_id,
    ).all()

    code_clean = account_code.strip().lower()

    # Exact match
    for e in entries:
        if str(e.code or "").lower() == code_clean:
            return {"exists": True, "entry": {"code": e.code, "name_en": e.name_en, "name_zh": e.name_zh}}

    # Suggest nearby codes (same prefix range)
    suggestions = [
        {"code": e.code, "name_en": e.name_en, "name_zh": e.name_zh}
        for e in sorted(entries, key=lambda x: str(x.code or ""))
        if str(e.code or "").startswith(code_clean[:2]) or str(e.code or "") == code_clean
    ][:5]

    return {"exists": False, "suggestions": suggestions}


def _is_rule_confirm_message(message: str) -> bool:
    """Return True if user message is a confirmation of a pending rule save."""
    lower = message.lower().strip()
    confirm_phrases = {
        "yes", "是", "ok", "確認", "confirm", "save", "save rule", "儲存規則",
        "儲存", "記住", "keep", "agree", "對", "好", "好的", "save it", "yes please",
    }
    return lower in confirm_phrases or any(phrase in lower for phrase in ("save rule", "儲存規則", "記住規則"))


def _detect_rule_intent(message: str) -> bool:
    """
    Return True if user's message is about accounting settings or rule creation.
    Only triggers when relevant, not on every chat message.
    """
    lower = message.lower()
    rule_keywords = [
        "account code", "account number", "gl account", "科目", "帳號",
        "should be", "always use", "treat as", "classify as", "categorize",
        "記為", "算作", "歸類", "分類為", "設為", "以後都", "下次",
        "這種", "這類", "this type", "these type", "same for all",
        "remember", "save rule", "儲存規則", "記住", "all future",
    ]
    return any(kw in lower for kw in rule_keywords)


def _parse_save_rule_block(content: str) -> tuple[str, dict | None]:
    """
    Extract <SAVE_RULE>{...}</SAVE_RULE> from AI reply.
    Returns (clean_reply, rule_dict | None).
    """
    pattern = re.compile(r"<SAVE_RULE>(.*?)</SAVE_RULE>", re.DOTALL | re.IGNORECASE)
    match = pattern.search(content)
    rule = None
    if match:
        raw = match.group(1).strip()
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            rule = json.loads(raw)
        except Exception:
            pass
    clean = pattern.sub("", content).strip()
    return clean, rule


def _extract_keyword_hint(message: str) -> str | None:
    """
    Pull a meaningful keyword from a natural-language remember command so the
    rule engine can match it against future OCR text.
    Looks for explicit vendor/company names or descriptive keywords.
    """
    import re as _re
    msg = message.lower()
    # Named entity: quoted string  e.g. remember "ABC Ltd" → account_code=4010
    m = _re.search(r'["\u201c\u201d\u300c\u300d]([^""\u201c\u201d\u300c\u300d]{2,60})["\u201c\u201d\u300c\u300d]', message)
    if m:
        return m.group(1).strip().lower()
    # "for [vendor/company name]" pattern
    m = _re.search(r'\bfor\s+([A-Z][A-Za-z0-9\s\-&\.]{2,40}?)(?:\s+invoice|\s+receipt|\s+vendor|$)', message)
    if m:
        return m.group(1).strip().lower()
    # Document-type keywords in the message itself
    for kw in ("invoice", "receipt", "發票", "收據", "expense", "purchase order", "費用"):
        if kw in msg:
            return kw
    return None


def _extract_doc_type_hint(message: str, mode: str) -> str | None:
    """
    Infer document type from message text or current processing mode.
    """
    msg = message.lower()
    if any(k in msg for k in ("invoice", "發票", "ar", "receivable")):
        return "invoice"
    if any(k in msg for k in ("receipt", "收據", "expense", "費用", "ap", "payable")):
        return "receipt"
    if any(k in msg for k in ("purchase order", "po", "採購")):
        return "purchase_order"
    # Fall back to processing mode
    mode_map = {"AR": "invoice", "AP": "receipt", "BANK": "bank"}
    return mode_map.get(mode)


def _build_context_message(
    *,
    mode: str,
    company_ctx: dict,
    coa: list[dict],
    transactions: list[dict],
    asset_records: list[dict] | None,
    request_context: dict[str, Any],
) -> dict:
    """
    Build a one-shot 'data injection' user+assistant exchange that sits at the
    top of every request (ahead of the compressible history).
    The assistant acknowledges receipt so the LLM treats it as established fact.
    """
    if mode == "RECON":
        recon = request_context.get("recon")
        payload: dict[str, Any] = {
            "mode": mode,
            "company": company_ctx,
            "chart_of_accounts": coa,
            "RECON_CONTEXT": recon if isinstance(recon, dict) else {},
        }
    elif mode == "REPORT":
        rep = request_context.get("report")
        payload = {
            "mode": mode,
            "company": company_ctx,
            "chart_of_accounts": coa,
            "REPORT_CONTEXT": rep if isinstance(rep, dict) else {},
        }
    elif mode == "OTHER" and asset_records is not None:
        payload = {
            "mode": mode,
            "company": company_ctx,
            "other_records": asset_records,
        }
    else:
        payload = {
            "mode": mode,
            "company": company_ctx,
            "chart_of_accounts": coa,
            "transactions": transactions,
        }
    return {
        "role": "user",
        "content": (
            "[CURRENT DATA]\n"
            "```json\n"
            + json.dumps(payload, ensure_ascii=False)
            + "\n```"
        ),
    }


def _call_llm(full_messages: list[dict]) -> tuple[str, dict]:
    """
    Synchronous HTTP call to DMXAPI.
    Returns (content_text, raw_response_dict) so callers can log token usage.
    """
    return deploy_chat_client.complete(full_messages)


def _parse_patches(content: str) -> tuple[str, list[TablePatch]]:
    """
    Extract <PATCHES>[…]</PATCHES> from the AI reply.
    Returns (clean_reply_text, patches).
    """
    patches: list[TablePatch] = []
    pattern = re.compile(r"<PATCHES>(.*?)</PATCHES>", re.DOTALL | re.IGNORECASE)
    match = pattern.search(content)

    if match:
        raw_json = match.group(1).strip()
        # Strip markdown fences if present
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
        raw_json = re.sub(r"\s*```$", "", raw_json)
        try:
            items = json.loads(raw_json)
            if isinstance(items, list):
                for item in items:
                    if (
                        isinstance(item, dict)
                        and "id_number" in item
                        and "field" in item
                        and "value" in item
                    ):
                        patches.append(
                            TablePatch(
                                id_number=str(item["id_number"]),
                                field=str(item["field"]),
                                value=item["value"],
                            )
                        )
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("[AiChat] Failed to parse patches JSON: %s", exc)

    clean_reply = pattern.sub("", content).strip()
    return clean_reply, patches


def _parse_recon_actions(content: str) -> tuple[str, list[ReconActionItem]]:
    """Extract <RECON_ACTIONS>[…]</RECON_ACTIONS> JSON array from the AI reply."""
    actions: list[ReconActionItem] = []
    pattern = re.compile(r"<RECON_ACTIONS>(.*?)</RECON_ACTIONS>", re.DOTALL | re.IGNORECASE)
    match = pattern.search(content)
    if match:
        raw_json = match.group(1).strip()
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
        raw_json = re.sub(r"\s*```$", "", raw_json)
        try:
            items = json.loads(raw_json)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("op"):
                        try:
                            actions.append(ReconActionItem.model_validate(item))
                        except Exception as exc:
                            logger.warning("[AiChat] Skip invalid RECON action: %s", exc)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("[AiChat] Failed to parse RECON_ACTIONS JSON: %s", exc)
    clean = pattern.sub("", content).strip()
    return clean, actions


def _parse_redirect_tasks(content: str) -> tuple[str, list[RedirectTaskItem]]:
    """Extract optional <REDIRECT_TASKS>[…]</REDIRECT_TASKS> JSON array."""
    out: list[RedirectTaskItem] = []
    pattern = re.compile(r"<REDIRECT_TASKS>(.*?)</REDIRECT_TASKS>", re.DOTALL | re.IGNORECASE)
    match = pattern.search(content)
    if match:
        raw_json = match.group(1).strip()
        raw_json = re.sub(r"^```(?:json)?\s*", "", raw_json)
        raw_json = re.sub(r"\s*```$", "", raw_json)
        try:
            items = json.loads(raw_json)
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and item.get("task_id"):
                        try:
                            out.append(RedirectTaskItem.model_validate(item))
                        except Exception as exc:
                            logger.warning("[AiChat] Skip invalid REDIRECT_TASK: %s", exc)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("[AiChat] Failed to parse REDIRECT_TASKS JSON: %s", exc)
    clean = pattern.sub("", content).strip()
    return clean, out


def _recon_allow_sets(recon_ctx: Any) -> tuple[set[str], set[str], set[str]]:
    if not isinstance(recon_ctx, dict):
        return set(), set(), set()
    banks = {str(x).strip() for x in _coerce_str_id_list(recon_ctx.get("allowed_bank_txn_ids"))}
    ledgers = {str(x).strip() for x in _coerce_str_id_list(recon_ctx.get("allowed_ledger_txn_ids"))}
    groups = {str(x).strip() for x in _coerce_str_id_list(recon_ctx.get("allowed_group_ids"))}
    return banks, ledgers, groups


def _recon_allowed_line_ids_from_context(recon_ctx: Any) -> set[str]:
    ids: set[str] = set()
    if not isinstance(recon_ctx, dict):
        return ids
    mgs = recon_ctx.get("matched_gl_summary")
    if not isinstance(mgs, list):
        return ids
    for g in mgs:
        if not isinstance(g, dict):
            continue
        for ln in g.get("draft_lines") or []:
            if isinstance(ln, dict):
                lid = ln.get("line_id")
                if lid not in (None, ""):
                    ids.add(str(lid).strip())
    return ids


def _filter_recon_actions_allowlist(
    actions: list[ReconActionItem],
    recon_ctx: Any,
) -> tuple[list[ReconActionItem], int]:
    """
    Drop actions with ids/group_id not in client-supplied allow-lists when those keys exist.
    Returns (kept_actions, dropped_count).
    """
    if not isinstance(recon_ctx, dict):
        return actions, 0
    if not any(
        k in recon_ctx
        for k in ("allowed_bank_txn_ids", "allowed_ledger_txn_ids", "allowed_group_ids")
    ):
        return actions, 0
    banks, ledgers, groups = _recon_allow_sets(recon_ctx)
    allowed_lines = _recon_allowed_line_ids_from_context(recon_ctx)
    kept: list[ReconActionItem] = []
    dropped = 0
    for act in actions:
        op = (act.op or "").lower().strip()
        ok = True
        if op == "match":
            for bid in act.bank_txn_ids:
                if str(bid).strip() not in banks:
                    ok = False
                    break
            if ok:
                for lid in act.ledger_txn_ids:
                    if str(lid).strip() not in ledgers:
                        ok = False
                        break
        elif op == "ledger_pending":
            for lid in act.ledger_txn_ids:
                if str(lid).strip() not in ledgers:
                    ok = False
                    break
        elif op == "unmatch":
            gid = (act.group_id or "").strip()
            if not gid or gid not in groups:
                ok = False
        elif op == "gl_draft_patch":
            gid = (act.group_id or "").strip()
            if not gid or gid not in groups:
                ok = False
            elif allowed_lines:
                for lid in act.deleted_line_ids:
                    ls = str(lid).strip()
                    if ls and ls not in allowed_lines:
                        ok = False
                        break
                if ok:
                    for ln in act.gl_lines:
                        lid = (ln.line_id or "").strip() if ln.line_id else ""
                        if lid and lid not in allowed_lines:
                            ok = False
                            break
        else:
            ok = False
        if ok:
            kept.append(act)
        else:
            dropped += 1
    return kept, dropped


async def _background_compress(
    session_id: str,
    messages: list[dict],
    mode: str,
    task_id: str,
    company_id: str,
) -> None:
    """Fire-and-forget background summarization with DB persistence."""
    try:
        compressed = await compress_messages(messages, mode)
        memory_store.save(session_id, compressed)
        logger.info("[AiChat] Background compression done for session %s", session_id)

        # Persist the summary so it survives server restarts
        summary_msgs = [
            m for m in compressed
            if str(m.get("content", "")).startswith("[SUMMARY")
        ]
        if summary_msgs:
            latest_summary = summary_msgs[-1]["content"]
            # Strip the prefix tag to store just the text
            import re as _re
            text = _re.sub(r"^\[SUMMARY[^\]]*\]:\s*", "", latest_summary).strip()
            real_count = sum(
                1 for m in messages
                if m.get("role") in ("user", "assistant")
                and not str(m.get("content", "")).startswith("[SUMMARY")
            )
            # Use a fresh DB session for the background task
            from app.database import SessionLocal
            bg_db = SessionLocal()
            try:
                memory_store.persist_summary(
                    session_id=session_id,
                    task_id=task_id,
                    mode=mode,
                    summary_text=text,
                    message_count=real_count,
                    db=bg_db,
                )
                # Also update company_id on the row
                from app.models.memory import SessionSummary
                row = (
                    bg_db.query(SessionSummary)
                    .filter(
                        SessionSummary.task_id == task_id,
                        SessionSummary.mode == mode,
                    )
                    .first()
                )
                if row and row.company_id == "default":
                    row.company_id = company_id
                    bg_db.commit()
            finally:
                bg_db.close()
    except Exception as exc:
        logger.error("[AiChat] Background compression failed: %s", exc)


# ── Endpoint ──────────────────────────────────────────────────────────────────

def _ensure_chat_task(
    db: Session,
    task_id: str,
    company_id: str,
    owner_user_id: str | None,
    processing_mode: str,
) -> None:
    """Ensure chat_tasks row exists so turns can be written to task_messages.

    The frontend may enqueue an AI chat job before POST /api/tasks completes (chat-only flow).
    """
    exists = (
        db.query(ChatTask.id)
        .filter(ChatTask.id == task_id, ChatTask.deleted_at.is_(None))
        .first()
    )
    if exists:
        return
    if not owner_user_id:
        return
    pm = (processing_mode or "AR")[:10]
    task = ChatTask(
        id=task_id,
        company_id=company_id,
        owner_user_id=owner_user_id,
        title="Chat",
        processing_mode=pm,
        status="idle",
    )
    try:
        with db.begin_nested():
            db.add(task)
            db.flush()
    except IntegrityError:
        pass


def _persist_messages(
    task_id: str,
    user_text: str,
    assistant_text: str,
    patches: list[Any],
    db: Session,
    *,
    assistant_overlay: dict[str, Any] | None = None,
    user_payload: dict[str, Any] | None = None,
) -> None:
    """Write the user + assistant turn to task_messages. Errors are logged, never raised."""
    try:
        task = db.query(ChatTask).filter(ChatTask.id == task_id).first()
        if not task:
            return  # task not yet created on backend — skip silently

        last = (
            db.query(TaskMessage)
            .filter(TaskMessage.task_id == task_id)
            .order_by(TaskMessage.sequence_index.desc())
            .first()
        )
        next_seq = (last.sequence_index + 1) if last else 0

        db.add(TaskMessage(
            id=str(uuid.uuid4()),
            task_id=task_id,
            sequence_index=next_seq,
            role="user",
            content_text=user_text,
            content_type="text",
            payload_json=user_payload,
        ))
        assistant_payload: dict[str, Any] | None = None
        if patches:
            assistant_payload = {"table_patches": [p.model_dump() for p in patches]}
        if assistant_overlay:
            assistant_payload = {**(assistant_payload or {}), **assistant_overlay}
        db.add(TaskMessage(
            id=str(uuid.uuid4()),
            task_id=task_id,
            sequence_index=next_seq + 1,
            role="assistant",
            content_text=assistant_text,
            content_type="text",
            payload_json=assistant_payload,
        ))
        db.commit()
    except Exception as exc:
        logger.warning("[AiChat] Failed to persist messages to DB: %s", exc)
        db.rollback()


@router.post("/api/ai-chat", response_model=AIChatResponse)
async def ai_chat(
    request: AIChatRequest,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: User = Depends(get_current_user),
) -> AIChatResponse:
    """AR/AP/BANK conversational AI with context injection and session memory."""
    async with long_running_db_work_slot():
        return await ai_chat_core(
            request, db, company_id, owner_user_id=current_user.id
        )


async def ai_chat_core(
    request: AIChatRequest,
    db: Session,
    company_id: str,
    *,
    owner_user_id: str | None = None,
) -> AIChatResponse:
    from app.core.processing_mode import normalize_processing_mode

    mode = normalize_processing_mode(request.mode)
    session_id = f"{request.session_id}_{mode}"

    # Extract task_id: request.session_id format is "{task_id}_{mode}" from frontend
    task_id = request.session_id.rsplit("_", 1)[0]

    # ── Abuse prevention ──────────────────────────────────────────────────────
    # Rate limit — document/recon/report chat modes get a higher per-minute allowance.
    _ocr_review_mode = mode in ("AR", "AP", "BANK", "OTHER", "RECON", "REPORT")
    rate_ok, rate_msg = await check_chat_rate_async(company_id, ocr_mode=_ocr_review_mode)
    if not rate_ok:
        raise HTTPException(status_code=429, detail=rate_msg)

    # Input validation + normalisation
    cleaned_message, msg_err = validate_chat_message(request.message)
    if msg_err:
        raise HTTPException(status_code=400, detail=msg_err)
    request = request.model_copy(update={"message": cleaned_message})

    # Monthly cost cap
    cost_ok, cost_msg = check_monthly_cost(db, company_id)
    if not cost_ok:
        raise HTTPException(status_code=429, detail=cost_msg)
    # cost_msg may be a warning (non-empty but cost_ok=True) — we'll append it to reply later
    _cost_warning = cost_msg if cost_ok and cost_msg else ""

    _ensure_chat_task(db, task_id, company_id, owner_user_id, mode)

    # 1. Load company context + CoA from DB
    company_ctx = _load_company_context(db, company_id)

    # 1b. Load rule memory for this mode — inject into AI context when relevant
    rule_memory_row = _load_rule_memory(db, company_id, mode)
    rule_memory_md = _rule_memory_md_for_prompt(rule_memory_row)

    # 1c. Load Company Manual (global business knowledge, always injected)
    company_manual_text = get_manual_for_injection(db, company_id)
    # 1d. Load Company Profile MD — injected when message has company-context keywords
    _profile_md = ""
    _profile_row = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    if _profile_row and _profile_row.profile_md:
        _profile_md = _profile_row.profile_md

    # For OTHER mode load asset records instead of transactions/CoA
    asset_records: list[dict] | None = None
    if mode == "OTHER":
        asset_records = _load_other_records(db, task_id, company_id)
        coa = []
    else:
        _coa_mode = mode if mode in ("AR", "AP", "BANK") else "AR"
        db_coa = _load_coa(db, company_id, _coa_mode)
        coa = db_coa if db_coa else request.context.get("coa", [])

    # 2. Load session history — try memory cache first, then DB on cache miss
    history = memory_store.get_or_load(session_id, task_id, db)

    # 3. Append the new user message to history
    history = list(history)  # copy to avoid mutating cached list
    history.append({"role": "user", "content": request.message})

    # 3b. Check for explicit "remember this rule" command (triggers immediate rule save to MD)
    remember_ctx = behavior_detector.parse_remember_command(session_id, request.message)
    _pending_rule: dict | None = None  # tracks a rule proposal awaiting user confirmation

    if remember_ctx is not None and mode not in ("RECON", "REPORT"):
        vendor = remember_ctx.get("vendor", "")
        field = remember_ctx.get("field", "")
        value = remember_ctx.get("value", "")
        if field and value:
            _pending_rule = {
                "type": "vendor" if vendor else "keyword",
                "vendor": vendor,
                "keywords": [_extract_keyword_hint(request.message) or field],
                "field": field,
                "value": value,
                "doc_type": _extract_doc_type_hint(request.message, mode),
            }

    # 3c. Check for confirmation of a pending rule (user said yes/是/confirm etc.)
    _rule_saved = False
    _rule_saved_msg = ""
    if mode not in ("RECON", "REPORT") and _is_rule_confirm_message(request.message):
        # Try to get pending rule from memory_store session state
        _session_state = memory_store.get_session_state(session_id) or {}
        _pending_from_state = _session_state.get("pending_rule")
        if _pending_from_state:
            _ok, _result = _save_rule_to_memory(
                db=db,
                company_id=company_id,
                mode=mode,
                rule_type=_pending_from_state.get("type", "vendor"),
                vendor=_pending_from_state.get("vendor"),
                keywords=_pending_from_state.get("keywords"),
                field=_pending_from_state.get("field", ""),
                value=_pending_from_state.get("value", ""),
                user_id="user_confirmed",
            )
            if _ok and _result != "rule_already_exists":
                _rule_saved = True
                _rule_saved_msg = (
                    f"已儲存規則：{_pending_from_state.get('vendor') or ''}「{_pending_from_state.get('field')}」= "
                    f"「{_pending_from_state.get('value')}」到 {mode} 規則記憶。"
                )
            elif _result == "rule_already_exists":
                _rule_saved = True
                _rule_saved_msg = "此規則已存在於記憶中，無需重複儲存。"
            # Clear the pending rule state
            _session_state.pop("pending_rule", None)
            memory_store.save_session_state(session_id, _session_state)

    # 3d. Check for pending behavior suggestions to inject into the system prompt
    behavior_suggestions = behavior_detector.get_pending_suggestions(session_id)

    # 4. Build the full messages array sent to the LLM:
    #    [system] + [data context exchange] + [compressed history]
    # Harden the base system prompt with anti-injection block
    _skill_mode = mode if mode in ("AR", "AP", "BANK", "OTHER", "RECON", "REPORT") else "AR"
    system_prompt = build_hardened_system_prompt(load_skill(_skill_mode))

    transactions = request.context.get("transactions", [])
    context_user_msg = _build_context_message(
        mode=mode,
        company_ctx=company_ctx,
        coa=coa,
        transactions=transactions,
        asset_records=asset_records,
        request_context=request.context,
    )
    context_ack_msg = {
        "role": "assistant",
        "content": "收到。我已收到目前資料，可以回答問題或修改表格。",
    }
    # Inject Company Manual (business context) — always injected when non-empty
    effective_system_prompt = system_prompt
    if company_manual_text:
        effective_system_prompt = (
            effective_system_prompt
            + "\n\n[COMPANY MANUAL — Business Knowledge]\n"
            + "以下是公司業務手冊，包含客戶資訊、供應商資訊、風險規則及業務背景。"
            + "請在回答所有問題時參考此手冊，以確保建議符合公司實際情況。\n\n"
            + company_manual_text
        )

    # Inject rule memory for OCR / asset modes only (not RECON/REPORT noise).
    _inject_rules = rule_memory_md and mode not in ("RECON", "REPORT") and (
        mode in ("AR", "AP", "BANK", "OTHER") or _detect_rule_intent(request.message)
    )
    if _inject_rules:
        _rule_header = "[COMPANY RULES MEMORY]\n"
        _rule_intro = (
            "以下是公司已儲存的規則內容。回答相關會計或分類問題時，請參考並優先套用這些規則。\n"
        )
        effective_system_prompt = (
            effective_system_prompt
            + "\n\n"
            + _rule_header
            + _rule_intro
            + (
                "若要建議儲存新規則，請在回覆末尾加入 "
                + '<SAVE_RULE>{"type":"vendor","vendor":"...","field":"...","value":"..."}</SAVE_RULE>'
                + " 標記。\n\n"
            )
            + rule_memory_md
        )

    # Inject Company Profile MD (CoA / accounting / GL triggers)
    if _profile_md and _should_inject_company_profile_md(cleaned_message, mode):
        effective_system_prompt = (
            effective_system_prompt
            + "\n\n[公司背景 / COMPANY PROFILE — Background Context]\n"
            + "以下為公司已存檔的背景說明（中文為主）；回答科目、分錄、行業或公司情境時請一併參考。\n"
            + "Company background on file (EN note): use for accounting, CoA, GL, and company-context questions.\n\n"
            + _profile_md
        )


    if mode in ("AR", "AP", "BANK"):
        effective_system_prompt = (
            effective_system_prompt
            + "\n\n[OCR JOURNALS — Draft double-entry]\n"
            + "Draft journals are stored per bank/ledger transaction via PUT /api/ocr-journals/{bank|ledger}/{txn_id}. "
            + "Lines must balance (debits = credits). Help users reason about accounts; table PATCHES still edit document rows.\n"
        )
    if behavior_suggestions and mode not in ("RECON", "REPORT"):
        suggestion_lines = []
        for s in behavior_suggestions:
            suggestion_lines.append(
                f"- 你注意到用戶已多次將「{s['vendor']}」的「{s['field']}」設定為「{s['value']}」"
                f"（已觀察 {s['count']} 次）。請在適當時機詢問用戶：「我注意到您經常將 {s['vendor']} "
                f"的{s['field']}設為 {s['value']}，是否要儲存為規則？（回覆「是」或「記住」即可）」"
            )
        effective_system_prompt = (
            effective_system_prompt
            + "\n\n[行為學習提示]\n"
            + "\n".join(suggestion_lines)
        )

    full_messages = (
        [{"role": "system", "content": effective_system_prompt}]
        + [context_user_msg, context_ack_msg]
        + history
    )

    # 5. Call the LLM (in thread pool to keep FastAPI non-blocking)
    loop = asyncio.get_event_loop()
    try:
        raw_reply, llm_response = await loop.run_in_executor(None, _call_llm, full_messages)
        log_token_usage(db, company_id, "ai_chat", settings.deploy_model, llm_response, task_id)
        # Token spike anomaly check (non-blocking)
        _usage = (llm_response or {}).get("usage", {})
        check_token_spike(db, company_id, _usage.get("total_tokens"))
    except requests.exceptions.HTTPError as exc:
        logger.error("[AiChat] LLM HTTP error: %s", exc)
        return AIChatResponse(reply="抱歉，AI 服務暫時不可用，請稍後再試。")
    except requests.exceptions.Timeout as exc:
        logger.error(
            "[AiChat] LLM timeout (connect=%ss read=%ss): %s",
            _DEPLOY_CONNECT_TIMEOUT,
            _DEPLOY_READ_TIMEOUT,
            exc,
        )
        return AIChatResponse(
            reply=(
                "AI 回覆逾時（等待上游超過時間上限）。請稍後再試；若經常發生，可調高 `DEPLOY_API_READ_TIMEOUT`（預設 300 秒）或縮短對話。\n\n"
                "The AI request timed out. Try again, or ask your admin to raise `DEPLOY_API_READ_TIMEOUT`, "
                "or use a shorter chat history."
            )
        )
    except Exception as exc:
        logger.error("[AiChat] LLM call failed: %s", exc)
        return AIChatResponse(reply="抱歉，發生錯誤，請稍後再試。")

    # Output scanner — block if LLM response contains sensitive data
    _scan_safe, raw_reply = scan_output(raw_reply, company_id)
    if not _scan_safe:
        return AIChatResponse(reply=raw_reply)

    # 6. Parse structured blocks (RECON actions first, then PATCHES)
    working = raw_reply
    recon_actions_out: list[ReconActionItem] = []
    redirect_tasks_out: list[RedirectTaskItem] = []

    if mode == "RECON":
        working, recon_actions_out = _parse_recon_actions(working)
        working, redirect_tasks_out = _parse_redirect_tasks(working)
        recon_ctx = request.context.get("recon")
        recon_actions_out, dropped_n = _filter_recon_actions_allowlist(recon_actions_out, recon_ctx)
        if dropped_n:
            working = (
                working
                + f"\n\n({dropped_n} AI proposal(s) removed: IDs not in allowed RECON lists.)"
            )

    clean_reply, patches = _parse_patches(working)
    if mode in ("RECON", "REPORT") and patches:
        logger.warning("[AiChat] Dropping unexpected PATCHES in mode=%s (count=%s)", mode, len(patches))
        patches = []

    # 6a-pre: Parse <SAVE_RULE> proposal block from AI reply
    clean_reply, ai_rule_proposal = _parse_save_rule_block(clean_reply)
    _save_rule_pending = False
    _save_rule_proposal_out: dict | None = None

    if mode not in ("RECON", "REPORT") and ai_rule_proposal and isinstance(ai_rule_proposal, dict):
        field = ai_rule_proposal.get("field", "")
        value = ai_rule_proposal.get("value", "")
        vendor = ai_rule_proposal.get("vendor", "")
        if field and value:
            # Validate account code against CoA if the field is account_code
            coa_validation = {}
            if field in ("account_code", "account", "gl_account") and value:
                coa_validation = _validate_account_code(db, company_id, mode, value)
                if not coa_validation.get("exists"):
                    suggestions_text = ", ".join(
                        f"{s['code']} ({s.get('name_en') or s.get('name_zh', '')})"
                        for s in coa_validation.get("suggestions", [])[:3]
                    )
                    if suggestions_text:
                        clean_reply += (
                            f"\n\n⚠️ 注意：科目代碼 {value} 在科目表中未找到。"
                            f"相近科目：{suggestions_text}"
                        )

            # Check dedup in MD
            rule_memory_row = _load_rule_memory(db, company_id, mode)
            current_md = rule_memory_row.content if rule_memory_row else ""
            already_exists = check_dedup(current_md, vendor or None, field, value)

            if already_exists:
                clean_reply += "\n\n✓ 此規則已存在於您的規則記憶中。"
            else:
                # Store proposal in session state for next confirmation
                _session_state = memory_store.get_session_state(session_id) or {}
                proposal = {
                    "type": ai_rule_proposal.get("type", "vendor"),
                    "vendor": vendor,
                    "keywords": ai_rule_proposal.get("keywords", []),
                    "field": field,
                    "value": value,
                }
                _session_state["pending_rule"] = proposal
                memory_store.save_session_state(session_id, _session_state)
                _save_rule_pending = True
                _save_rule_proposal_out = proposal

    # Also handle immediate explicit remember command (from step 3b)
    if mode not in ("RECON", "REPORT") and _pending_rule and not _rule_saved:
        field = _pending_rule.get("field", "")
        value = _pending_rule.get("value", "")
        vendor = _pending_rule.get("vendor", "")
        if field and value:
            # Check CoA validity
            coa_validation = {}
            if field in ("account_code", "account", "gl_account") and value:
                coa_validation = _validate_account_code(db, company_id, mode, value)

            if coa_validation.get("exists") is False and coa_validation.get("suggestions"):
                # Let AI handle the CoA question — store pending rule but don't save yet
                _session_state = memory_store.get_session_state(session_id) or {}
                _session_state["pending_rule"] = _pending_rule
                memory_store.save_session_state(session_id, _session_state)
                _save_rule_pending = True
                _save_rule_proposal_out = _pending_rule
            else:
                # Save immediately (user was explicit)
                _ok, _result = _save_rule_to_memory(
                    db=db,
                    company_id=company_id,
                    mode=mode,
                    rule_type=_pending_rule.get("type", "vendor"),
                    vendor=_pending_rule.get("vendor"),
                    keywords=_pending_rule.get("keywords"),
                    field=field,
                    value=value,
                    user_id="user_explicit",
                )
                if _ok and _result != "rule_already_exists":
                    _rule_saved = True
                    _rule_saved_msg = f"已儲存規則到 {mode} 規則記憶。"

    # 6a. Apply PATCHES to other_records for OTHER mode
    if mode == "OTHER" and patches:
        try:
            from app.models.other import OtherRecord as _ALR
            from app.services.other_sync_service import sync_record as _sync
            for patch in patches:
                al_rec = (
                    db.query(_ALR)
                    .filter(
                        _ALR.id == patch.id_number,
                        _ALR.company_id == company_id,
                    )
                    .first()
                )
                if al_rec:
                    al_rec.payload_json = {
                        **(al_rec.payload_json or {}),
                        patch.field: patch.value,
                    }
                    db.flush()
                    _sync(al_rec, db)
        except Exception as exc:
            logger.warning("[AiChat] OTHER patch apply failed: %s", exc)

    # 6b. Record edits for behavior learning
    if mode not in ("RECON", "REPORT"):
        transactions = request.context.get("transactions", [])
        txn_map = {str(t.get("id_number", t.get("id", ""))): t for t in transactions}
        for patch in patches:
            txn_ctx = txn_map.get(str(patch.id_number))
            behavior_detector.record_edit(
                session_id=session_id,
                patch={"id_number": patch.id_number, "field": patch.field, "value": patch.value},
                transaction_context=txn_ctx,
            )

    # 6c. If user confirmed a behavior suggestion, save to MD memory (not DB)
    lower_msg = request.message.lower().strip()
    if (
        mode not in ("RECON", "REPORT")
        and lower_msg in ("是", "yes", "ok", "確認", "記住", "save", "confirm")
        and not _rule_saved
    ):
        last_edit = behavior_detector._last_edit.get(session_id)
        if last_edit and last_edit.get("field") and last_edit.get("value"):
            _ok, _result = _save_rule_to_memory(
                db=db,
                company_id=company_id,
                mode=mode,
                rule_type="vendor" if last_edit.get("vendor") else "keyword",
                vendor=last_edit.get("vendor"),
                keywords=[last_edit.get("vendor") or last_edit.get("field")] if not last_edit.get("vendor") else None,
                field=last_edit.get("field", ""),
                value=last_edit.get("value", ""),
                user_id="user_confirmed",
            )
            if _ok and _result != "rule_already_exists":
                _rule_saved = True
                _rule_saved_msg = (
                    f"已儲存規則：{last_edit.get('vendor') or ''}「{last_edit.get('field')}」"
                    f"= 「{last_edit.get('value')}」到 {mode} 規則記憶。"
                )

    # 7. Save assistant reply to in-memory history
    history.append({"role": "assistant", "content": raw_reply})
    memory_store.save(session_id, history)

    # 8. Persist both turns to DB (non-blocking, errors swallowed)
    _user_msg_payload: dict[str, Any] | None = None
    _persist_messages(
        task_id,
        request.message,
        clean_reply,
        patches,
        db,
        user_payload=_user_msg_payload,
    )

    # 9. Trigger background compression if threshold reached (async, non-blocking)
    if SUMMARIZE_CONFIG.get("async_mode") and should_summarize(history):
        asyncio.create_task(
            _background_compress(session_id, history, mode, task_id, company_id)
        )

    # Append cost warning to reply if threshold was reached
    if _cost_warning:
        clean_reply = clean_reply + f"\n\n---\n💡 {_cost_warning}"

    return AIChatResponse(
        reply=clean_reply,
        table_patches=patches,
        save_rule_pending=_save_rule_pending,
        save_rule_proposal=_save_rule_proposal_out,
        rule_saved=_rule_saved,
        rule_saved_message=_rule_saved_msg,
        recon_actions=recon_actions_out if mode == "RECON" else [],
        redirect_tasks=redirect_tasks_out if mode == "RECON" else [],
        recon_redirect=None,
    )


# ── AI Title generation ───────────────────────────────────────────────────────

class AiTitleRequest(BaseModel):
    messages: list[dict]
    mode: str = Field(default="AR", max_length=MAX_AI_CHAT_MODE_CHARS)

    @model_validator(mode="after")
    def _limit_title_messages(self):
        if len(self.messages) > MAX_AI_TITLE_MESSAGES:
            raise ValueError(f"too many messages (max {MAX_AI_TITLE_MESSAGES})")
        for m in self.messages:
            if not isinstance(m, dict):
                continue
            c = m.get("content")
            if isinstance(c, str) and len(c) > MAX_AI_TITLE_MESSAGE_CONTENT_CHARS:
                raise ValueError("message content too long for title generation")
        return self


class AiTitleResponse(BaseModel):
    title: str


@router.post("/api/ai-title", response_model=AiTitleResponse)
async def generate_title(
    request: AiTitleRequest,
    _: str = Depends(get_current_company_id),
) -> AiTitleResponse:
    """
    Generate a short task title from the first few messages of a conversation.
    Fire-and-forget from the frontend; errors fall back to a generic title.
    """
    prompt_messages: list[dict] = [
        {
            "role": "system",
            "content": (
                "你是任務標題生成器。"
                "根據以下對話內容，只需回覆一個簡短標題（最多8個字，優先使用繁體中文）。"
                "不要加標點符號在結尾，不要解釋，只輸出標題本身。"
            ),
        },
        *request.messages[:6],
        {"role": "user", "content": "請為以上對話生成一個簡短標題。"},
    ]
    loop = asyncio.get_event_loop()
    try:
        raw, _resp = await loop.run_in_executor(None, _call_llm, prompt_messages)
        title = raw.strip().split("\n")[0][:60]
    except Exception as exc:
        logger.warning("[AiTitle] Title generation failed: %s", exc)
        title = f"{request.mode} 對話"
    return AiTitleResponse(title=title)

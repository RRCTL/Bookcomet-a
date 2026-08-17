"""
Company Manual Service
======================
Loads the Company Manual for a given company and returns a compact
injection-ready string for use in AI prompts.

If the manual is short (<= INLINE_THRESHOLD chars) it is included verbatim.
If it is longer, it is automatically summarised by the LLM so it never
bloats the context window of every AI call.

Summaries are cached in RAM (per company) and invalidated whenever the
manual version number changes.
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

import requests
from sqlalchemy.orm import Session

from app.models.company_manual import CompanyManual
from app.models.company_context import CompanyRule
from app.core.config import settings
from app.services.abuse_guard import scan_output
from app.services.rule_governance import RULE_TYPE_COMPANY_CONTEXT

logger = logging.getLogger(__name__)

# ── Tuning constants ──────────────────────────────────────────────────────────

# Manuals up to this many characters are injected verbatim
INLINE_THRESHOLD = 2000

# Summaries are capped at this many characters
SUMMARY_MAX_CHARS = 800

_DEPLOY_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
_DEPLOY_BASE_URL = (
    os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or "https://www.dmxapi.cn"
).rstrip("/")


# ── In-memory summary cache ───────────────────────────────────────────────────

class _SummaryCache:
    """Thread-safe per-company cache: {company_id: (version, summary_text)}"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, tuple[int, str]] = {}

    def get(self, company_id: str, version: int) -> Optional[str]:
        with self._lock:
            entry = self._data.get(company_id)
            if entry and entry[0] == version:
                return entry[1]
        return None

    def set(self, company_id: str, version: int, summary: str) -> None:
        with self._lock:
            self._data[company_id] = (version, summary)

    def invalidate(self, company_id: str) -> None:
        with self._lock:
            self._data.pop(company_id, None)


_cache = _SummaryCache()


# ── Core function ─────────────────────────────────────────────────────────────

def _ctx_cache_key(company_id: str) -> str:
    return f"{company_id}:knowledge_context"


def _context_version_from_rule(row: CompanyRule) -> int:
    if row.updated_at is not None:
        return int(row.updated_at.timestamp())
    return hash((row.rule_json or {}).get("body") or "") % (2**31)


def get_manual_for_injection(db: Session, company_id: str) -> str:
    """
    Return a compact, injection-ready string for AI prompts.

    Prefers the Knowledge **company_context** CompanyRule body; falls back to
    legacy Company Manual (summarised when long).
    """
    ctx_row = (
        db.query(CompanyRule)
        .filter(
            CompanyRule.company_id == company_id,
            CompanyRule.rule_type == RULE_TYPE_COMPANY_CONTEXT,
            CompanyRule.is_active.is_(True),
        )
        .first()
    )
    if ctx_row and isinstance(ctx_row.rule_json, dict):
        body = (ctx_row.rule_json.get("body") or "").strip()
        if body:
            ckey = _ctx_cache_key(company_id)
            ver = _context_version_from_rule(ctx_row)
            if len(body) <= INLINE_THRESHOLD:
                return body
            cached = _cache.get(ckey, ver)
            if cached is not None:
                return cached
            summary = _summarise_manual(body)
            _cache.set(ckey, ver, summary)
            return summary

    row = db.query(CompanyManual).filter(CompanyManual.company_id == company_id).first()
    if not row or not row.content or not row.content.strip():
        return ""

    content = row.content.strip()
    version = row.version

    if len(content) <= INLINE_THRESHOLD:
        return content

    cached = _cache.get(company_id, version)
    if cached is not None:
        return cached

    summary = _summarise_manual(content)
    _cache.set(company_id, version, summary)
    return summary


def _summarise_manual(content: str) -> str:
    """
    Call the LLM to produce a compact summary of the Company Manual.
    Falls back to a simple truncation if the LLM call fails.
    """
    if not _DEPLOY_API_KEY:
        return content[:SUMMARY_MAX_CHARS] + "\n...(manual truncated)"

    prompt = (
        "You are a Hong Kong accounting assistant. Summarise the following Company Manual "
        f"into at most {SUMMARY_MAX_CHARS} characters. Preserve ALL factual information: "
        "client names, risk thresholds, payment terms, seasonal patterns, and glossary terms. "
        "Use bullet points. Output ONLY the summary, no preamble.\n\n"
        f"MANUAL:\n{content}"
    )
    try:
        resp = requests.post(
            f"{_DEPLOY_BASE_URL}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {_DEPLOY_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.deploy_model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 400,
                "temperature": 0.2,
            },
            timeout=(10, 60),
            verify=True,
        )
        if resp.status_code == 200:
            raw_summary = resp.json()["choices"][0]["message"]["content"].strip()
            _safe, raw_summary = scan_output(raw_summary)
            if _safe:
                return raw_summary
    except Exception as exc:
        logger.warning("[CompanyManualService] Summarisation failed: %s", exc)

    # Fallback: plain truncation
    return content[:SUMMARY_MAX_CHARS] + "\n...(manual truncated)"

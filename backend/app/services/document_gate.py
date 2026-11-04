"""
Document Gate — Stage 0 classifier that runs before OCR enhancement.

Classification results:
  TRANSACTIONAL       — receipt / invoice / cheque → proceed with AR/AP pipeline
  REFERENCE_FINANCIAL — loan schedule / asset purchase / mortgage → route to OTHER
  NON_FINANCIAL       — no usable financial data → reject with message
  AMBIGUOUS           — unclear → ask user

Three-layer approach (cheapest first):
  1. CompanyRule with rule_type="document_gate" (zero LLM cost)
  2. Keyword pre-filter (zero LLM cost)
  3. Fast LLM call using deepseek-chat (~100-200 tokens)
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import requests

from app.core.config import settings, resolved_gate_llm_model
from app.core.gateway_settings import openai_chat_completions_url

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# ── Result constants ──────────────────────────────────────────────────────────
TRANSACTIONAL = "TRANSACTIONAL"
REFERENCE_FINANCIAL = "REFERENCE_FINANCIAL"
NON_FINANCIAL = "NON_FINANCIAL"
AMBIGUOUS = "AMBIGUOUS"

# ── Keyword lists ─────────────────────────────────────────────────────────────

_REFERENCE_KEYWORDS = [
    # Loan / mortgage
    "分期付款", "月供", "還款期", "還款計劃", "還款表", "攤還", "攤還表",
    "貸款協議", "按揭", "抵押", "利息支出", "利率", "本金餘額",
    "amortization", "amortisation", "installment schedule", "repayment schedule",
    "loan agreement", "mortgage", "hire purchase", "hp agreement",
    "monthly repayment", "outstanding principal", "interest rate",
    "年利率", "月利率",
    # Asset purchase / vehicle / property
    "買賣合約", "購車協議", "車輛購置", "物業買賣", "樓宇買賣",
    "sale and purchase agreement", "spa", "vehicle purchase", "property purchase",
    "asset acquisition", "depreciation schedule", "折舊計劃", "折舊表",
    "殘值", "使用年限", "耐用年限",
    # Lease
    "租賃協議", "融資租賃", "operating lease", "finance lease",
]

_TRANSACTIONAL_STRONG = [
    "invoice", "receipt", "cheque", "check", "支票", "收據", "發票",
    "payment slip", "bank-in slip", "存款單", "入賬單",
    "purchase order", "po number",
]

_NON_FINANCIAL_KEYWORDS = [
    "employment contract", "雇傭合約", "employment agreement",
    "nda", "non-disclosure", "保密協議",
    "passport", "hkid", "身份證",
]


def _keyword_classify(ocr_text: str) -> str | None:
    """
    Fast keyword pre-filter.  Returns a classification string or None if
    the keywords are ambiguous / absent.
    """
    text = ocr_text.lower()

    # Strong transactional signals
    transactional_hits = sum(1 for k in _TRANSACTIONAL_STRONG if k in text)
    reference_hits = sum(1 for k in _REFERENCE_KEYWORDS if k in text)
    non_financial_hits = sum(1 for k in _NON_FINANCIAL_KEYWORDS if k in text)

    if non_financial_hits >= 2:
        return NON_FINANCIAL

    if reference_hits >= 2 and transactional_hits == 0:
        return REFERENCE_FINANCIAL

    if transactional_hits >= 1 and reference_hits == 0:
        return TRANSACTIONAL

    return None  # inconclusive → fall through to LLM


def _rule_classify(ocr_text: str, company_id: str, db: "Session") -> str | None:
    """
    Check company-specific document_gate rules.
    Returns a classification string or None if no rule matched.
    """
    try:
        from app.models.company_context import CompanyRule

        rules = (
            db.query(CompanyRule)
            .filter(
                CompanyRule.company_id == company_id,
                CompanyRule.rule_type == "document_gate",
                CompanyRule.is_active.is_(True),
            )
            .order_by(CompanyRule.priority.asc())
            .all()
        )
        text = ocr_text.lower()
        for rule in rules:
            pattern = (rule.keyword_pattern or "").lower()
            if pattern and pattern in text:
                result = (rule.rule_json or {}).get("classification")
                if result in (TRANSACTIONAL, REFERENCE_FINANCIAL, NON_FINANCIAL, AMBIGUOUS):
                    logger.info(
                        "[Gate] Rule '%s' matched → %s", rule.rule_name, result
                    )
                    return result
    except Exception as exc:
        logger.warning("[Gate] Rule check failed: %s", exc)
    return None


def _llm_classify(ocr_text: str) -> str:
    """
    Fast LLM call to classify the document.  Uses only the first 1000 chars
    of OCR text to keep cost minimal (~100-200 tokens).
    """
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
    base_url = (
        os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or os.getenv("VLM_BASE_URL") or ""
    ).rstrip("/")
    model = resolved_gate_llm_model(settings)

    snippet = ocr_text[:1000].strip()
    system_prompt = (
        "You are a financial document classifier. Classify the document into exactly ONE category:\n"
        "- TRANSACTIONAL: routine AR/AP document (invoice, receipt, cheque, payment slip, purchase order)\n"
        "- REFERENCE_FINANCIAL: loan schedule, asset purchase agreement, mortgage, hire purchase, "
        "depreciation schedule, vehicle/property purchase document\n"
        "- NON_FINANCIAL: no usable financial data (employment contract, ID, general correspondence)\n"
        "- AMBIGUOUS: cannot determine from available text\n\n"
        "Return ONLY ONE WORD from the list above. No explanation."
    )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": f"Document OCR text:\n{snippet}",
            },
        ],
        "max_tokens": 10,
        "temperature": 0.0,
    }
    try:
        resp = requests.post(
            openai_chat_completions_url(base_url),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(10, 30),
            verify=True,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data["choices"][0]["message"]["content"].strip().upper()
        # Normalise — model might return "REFERENCE_FINANCIAL." etc.
        for label in (TRANSACTIONAL, REFERENCE_FINANCIAL, NON_FINANCIAL, AMBIGUOUS):
            if label in result:
                logger.info("[Gate] LLM classified → %s (raw: %r)", label, result)
                return label
        logger.warning("[Gate] LLM returned unexpected: %r, defaulting TRANSACTIONAL", result)
        return TRANSACTIONAL
    except Exception as exc:
        logger.warning("[Gate] LLM classify failed: %s — defaulting TRANSACTIONAL", exc)
        return TRANSACTIONAL


def classify_document(
    ocr_text: str,
    company_id: str = "default",
    db: "Session | None" = None,
) -> str:
    """
    Main entry-point.  Returns one of: TRANSACTIONAL, REFERENCE_FINANCIAL,
    NON_FINANCIAL, AMBIGUOUS.

    Classification order (cheapest first):
      1. Company-specific document_gate rules (DB lookup)
      2. Keyword pre-filter (pure Python)
      3. LLM classifier (remote call)
    """
    if not ocr_text or not ocr_text.strip():
        return TRANSACTIONAL

    # Layer 1: company rules
    if db is not None:
        rule_result = _rule_classify(ocr_text, company_id, db)
        if rule_result:
            return rule_result

    # Layer 2: keyword filter
    kw_result = _keyword_classify(ocr_text)
    if kw_result:
        return kw_result

    # Layer 3: LLM
    return _llm_classify(ocr_text)


# Human-readable messages for the frontend
GATE_MESSAGES = {
    REFERENCE_FINANCIAL: (
        "This document appears to be a financial reference document (e.g. loan schedule, "
        "asset purchase agreement, or mortgage statement) rather than a routine receipt or invoice. "
        "How would you like to proceed?"
    ),
    NON_FINANCIAL: (
        "This document does not appear to contain financial transaction data. "
        "It may be a contract, ID document, or general correspondence."
    ),
    AMBIGUOUS: (
        "This document is unclear. It may contain financial data, but we could not confidently "
        "classify it. How would you like to proceed?"
    ),
}

# Sub-type hints used to route REFERENCE_FINANCIAL documents
SUBTYPE_KEYWORDS = {
    "loan": [
        "loan", "mortgage", "hire purchase", "hp agreement",
        "amortization", "repayment schedule", "installment schedule",
        "貸款", "按揭", "分期付款", "還款計劃",
    ],
    "fixed_asset": [
        "vehicle purchase", "property purchase", "sale and purchase",
        "asset acquisition", "depreciation", "useful life",
        "購車", "物業買賣", "買賣合約", "折舊",
    ],
}


def infer_document_subtype(ocr_text: str) -> str:
    """
    For REFERENCE_FINANCIAL documents, guess 'loan' or 'fixed_asset'.
    Returns 'loan' by default if unclear.
    """
    text = ocr_text.lower()
    loan_hits = sum(1 for k in SUBTYPE_KEYWORDS["loan"] if k in text)
    asset_hits = sum(1 for k in SUBTYPE_KEYWORDS["fixed_asset"] if k in text)
    return "fixed_asset" if asset_hits > loan_hits else "loan"

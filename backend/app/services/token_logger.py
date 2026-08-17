"""
Token usage logging helper.

Parses the 'usage' field from LLM API responses and writes a TokenUsageLog
row to the database.  Called after every LLM call across the codebase.

Model pricing table (USD per 1M tokens, input/output) — update as needed.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# USD cost per 1M tokens (input, output) — approximate 2026 pricing
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    "deepseek-chat": (0.14, 0.28),
    "DeepSeek-V3.2": (0.14, 0.28),
    "DeepSeek-V3.2-Thinking": (0.55, 2.19),
    "deepseek-reasoner": (0.55, 2.19),
    "qwen3.5-plus-2026-02-15": (0.21, 0.63),
}


def log_token_usage(
    db: Any,
    company_id: str,
    call_type: str,
    model: str,
    api_response: dict[str, Any] | None,
    task_id: str | None = None,
) -> None:
    """
    Extract token counts from an API response dict and persist to DB.
    Errors are swallowed — token logging must never break normal flow.

    api_response: the raw dict returned by the LLM API (contains 'usage' key)
    call_type: descriptive tag, e.g. 'ocr_enhance', 'ai_chat', 'summarize',
               'gate_classify', 'title', 'asset_extract'
    """
    if db is None or api_response is None:
        return
    try:
        from app.models.memory import TokenUsageLog

        usage = api_response.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens")
        completion_tokens = usage.get("completion_tokens")
        total_tokens = usage.get("total_tokens")

        # Estimate cost
        cost_usd: float | None = None
        pricing = _MODEL_PRICING.get(model)
        if pricing and prompt_tokens is not None and completion_tokens is not None:
            input_cost = (prompt_tokens / 1_000_000) * pricing[0]
            output_cost = (completion_tokens / 1_000_000) * pricing[1]
            cost_usd = round(input_cost + output_cost, 8)

        db.add(
            TokenUsageLog(
                id=str(uuid.uuid4()),
                company_id=company_id or "default",
                task_id=task_id,
                call_type=call_type,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                estimated_cost_usd=cost_usd,
            )
        )
        db.commit()
        logger.debug(
            "[TokenLog] %s model=%s tokens=%s cost=$%s",
            call_type,
            model,
            total_tokens,
            cost_usd,
        )
    except Exception as exc:
        logger.warning("[TokenLog] Failed to log token usage: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass

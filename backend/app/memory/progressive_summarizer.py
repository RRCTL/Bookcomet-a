"""
Progressive Summarization for AR/AP/BANK Chat Sessions
Compresses old conversation history while preserving recent turns in full.
Based on the Progressive Summarization spec (2026-02-28).

Persistence: summaries are written to session_summaries table (DB) so AI
memory survives server restarts.  On session start, the last persisted
summary is loaded as a [SUMMARY] prefix message.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import Any, TYPE_CHECKING

import requests

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────────────

SUMMARIZE_CONFIG: dict[str, Any] = {
    "trigger_rounds": 15,       # Trigger after 15 complete rounds (user+assistant)
    "keep_recent_rounds": 5,    # Keep 5 most recent rounds verbatim after compression
    "max_summary_tokens": 400,  # Max tokens for the generated summary
    "stacking_mode": "append",  # "append" | "replace" — append preserves full history
    "async_mode": True,         # True = background compression, False = synchronous
    "summarize_model": settings.deploy_model,
}

SUMMARIZE_SYSTEM_PROMPTS = {
    "AR": (
        "You are summarizing a conversation between a user and an Accounts Receivable AI assistant. "
        "Create a structured summary that MUST preserve:\n"
        "1. Specific invoice/voucher numbers mentioned (e.g. INV-001, AR-20250101-001)\n"
        "2. Customer/payer names and their status\n"
        "3. Decisions made (e.g. account code changes, category assignments)\n"
        "4. Unresolved issues or pending actions\n"
        "5. Any amounts, dates, or account codes discussed\n"
        "6. User's special instructions or preferences\n"
        "Format as a concise paragraph with specific facts. Max 400 tokens."
    ),
    "AP": (
        "You are summarizing a conversation between a user and an Accounts Payable AI assistant. "
        "Create a structured summary that MUST preserve:\n"
        "1. Vendor names and related invoice/voucher numbers\n"
        "2. Payment decisions made and account code assignments\n"
        "3. Disputed amounts or discrepancies identified\n"
        "4. Upcoming payment deadlines discussed\n"
        "5. Any amounts, dates, or account codes discussed\n"
        "6. User's cash flow concerns or special instructions\n"
        "Format as a concise paragraph with specific facts. Max 400 tokens."
    ),
    "BANK": (
        "You are summarizing a conversation between a user and a Bank Reconciliation AI assistant. "
        "Create a structured summary that MUST preserve:\n"
        "1. Specific unmatched transactions discussed (date, amount, reference)\n"
        "2. Reconciliation decisions made\n"
        "3. Outstanding items still unresolved\n"
        "4. Any patterns or anomalies identified\n"
        "5. Adjusting entries or corrections agreed upon\n"
        "Format as a concise paragraph with specific facts. Max 400 tokens."
    ),
}

_DEFAULT_SUMMARY_PROMPT = SUMMARIZE_SYSTEM_PROMPTS["AR"]


# ── Core logic ────────────────────────────────────────────────────────────────

def should_summarize(messages: list[dict]) -> bool:
    """Return True when the conversation has grown past the trigger threshold."""
    real = [
        m for m in messages
        if m.get("role") != "system"
        and not str(m.get("content", "")).startswith("[SUMMARY")
    ]
    rounds = len(real) // 2
    return rounds >= SUMMARIZE_CONFIG["trigger_rounds"]


def split_messages_for_summarization(
    messages: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Split messages into three buckets:
      prefix   — system messages + existing summaries (always kept verbatim)
      to_summarize — old rounds to be compressed
      to_keep  — most recent N rounds kept verbatim
    """
    keep_count = SUMMARIZE_CONFIG["keep_recent_rounds"] * 2

    system_messages = [m for m in messages if m.get("role") == "system"]
    summary_messages = [
        m for m in messages
        if m.get("role") != "system"
        and str(m.get("content", "")).startswith("[SUMMARY")
    ]
    real_messages = [
        m for m in messages
        if m.get("role") != "system"
        and not str(m.get("content", "")).startswith("[SUMMARY")
    ]

    if len(real_messages) <= keep_count:
        return system_messages + summary_messages, [], real_messages

    to_summarize = real_messages[:-keep_count]
    to_keep = real_messages[-keep_count:]
    return system_messages + summary_messages, to_summarize, to_keep


def _call_llm_sync(messages: list[dict], max_tokens: int) -> str:
    """Synchronous LLM call used during summarization (runs in a thread pool)."""
    api_key = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
    base_url = (
        os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or "https://www.dmxapi.cn"
    ).rstrip("/")
    model = SUMMARIZE_CONFIG["summarize_model"]

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.1,
    }
    try:
        resp = requests.post(
            f"{base_url}/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=(15, 60),
            verify=True,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.error("[Memory] Summarization LLM call failed: %s", exc)
        real_count = sum(
            1 for m in messages
            if m.get("role") in ("user", "assistant")
        )
        return f"[SUMMARY — auto-generated]: Earlier conversation covered {real_count} messages."


async def generate_summary(to_summarize: list[dict], mode: str) -> str:
    """Call the LLM in a thread pool to generate a summary of old turns."""
    system_prompt = SUMMARIZE_SYSTEM_PROMPTS.get(mode, _DEFAULT_SUMMARY_PROMPT)
    conversation_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in to_summarize
    )
    llm_messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": f"Please summarize the following conversation:\n\n{conversation_text}",
        },
    ]
    loop = asyncio.get_event_loop()
    summary_text = await loop.run_in_executor(
        None,
        _call_llm_sync,
        llm_messages,
        SUMMARIZE_CONFIG["max_summary_tokens"],
    )
    return summary_text


async def compress_messages(messages: list[dict], mode: str) -> list[dict]:
    """
    Main entry-point: compress old conversation history.
    Returns the compressed messages list (or the original if threshold not reached).
    """
    if not should_summarize(messages):
        return messages

    logger.info("[Memory] Triggering progressive summarization for mode=%s", mode)
    prefix_messages, to_summarize, to_keep = split_messages_for_summarization(messages)

    if not to_summarize:
        return messages

    new_summary_text = await generate_summary(to_summarize, mode)
    new_summary_message = {
        "role": "assistant",
        "content": f"[SUMMARY — Conversation history up to this point]: {new_summary_text}",
    }

    if SUMMARIZE_CONFIG["stacking_mode"] == "append":
        compressed = prefix_messages + [new_summary_message] + to_keep
    else:
        system_only = [m for m in prefix_messages if m.get("role") == "system"]
        compressed = system_only + [new_summary_message] + to_keep

    logger.info(
        "[Memory] Compression complete: %d → %d messages (%.0f%% reduction)",
        len(messages),
        len(compressed),
        (1 - len(compressed) / len(messages)) * 100,
    )
    return compressed


# ── Storage layer ─────────────────────────────────────────────────────────────

class MemoryStore:
    """
    In-memory session storage with two persistence layers:

    1. Cache-miss recovery: on restart, rehydrates from task_messages in DB.
    2. Summary persistence: cross-session summaries are stored in
       session_summaries table so the AI retains memory across server restarts.

    Keyed by session_id (e.g. "{task_id}_{MODE}").
    """

    def __init__(self) -> None:
        self._store: dict[str, list[dict]] = {}
        self._state: dict[str, dict] = {}  # per-session state (pending rules, etc.)

    def get(self, session_id: str) -> list[dict]:
        return self._store.get(session_id, [])

    def save(self, session_id: str, messages: list[dict]) -> None:
        self._store[session_id] = messages

    def clear(self, session_id: str) -> None:
        self._store.pop(session_id, None)
        self._state.pop(session_id, None)

    def get_session_state(self, session_id: str) -> dict:
        """Return per-session state dict (pending rules, confirmations, etc.)."""
        return self._state.get(session_id, {})

    def save_session_state(self, session_id: str, state: dict) -> None:
        """Persist per-session state dict in memory."""
        self._state[session_id] = state

    def get_or_load(self, session_id: str, task_id: str, db: Any) -> list[dict]:
        """
        Return in-memory history if present.  On cache miss (server restart):
        1. Check session_summaries for a persisted cross-session summary.
        2. Load recent task_messages from DB to reconstruct context window.
        """
        if session_id in self._store:
            return self._store[session_id]

        history: list[dict] = []

        try:
            from app.models.chat import TaskMessage
            from app.models.memory import SessionSummary

            # --- Layer 1: load persisted cross-session summary ---
            # session_id format: "{task_id}_{MODE}"
            parts = session_id.rsplit("_", 1)
            mode = parts[-1] if len(parts) == 2 else "AR"

            summary_row = (
                db.query(SessionSummary)
                .filter(
                    SessionSummary.task_id == task_id,
                    SessionSummary.mode == mode,
                )
                .order_by(SessionSummary.updated_at.desc())
                .first()
            )
            if summary_row:
                history.append({
                    "role": "assistant",
                    "content": (
                        f"[SUMMARY — Previous session memory]: {summary_row.summary_text}"
                    ),
                })
                logger.info(
                    "[Memory] Loaded persisted summary for task=%s mode=%s (%d chars)",
                    task_id,
                    mode,
                    len(summary_row.summary_text),
                )

            # --- Layer 2: load recent messages from DB ---
            keep_rounds = SUMMARIZE_CONFIG.get("keep_recent_rounds", 5)
            limit = keep_rounds * 2 + 2

            rows = (
                db.query(TaskMessage)
                .filter(TaskMessage.task_id == task_id)
                .order_by(TaskMessage.sequence_index.desc())
                .limit(limit)
                .all()
            )
            rows = list(reversed(rows))
            recent = [
                {"role": r.role, "content": r.content_text or ""}
                for r in rows
                if r.role in ("user", "assistant")
            ]
            history.extend(recent)

            self._store[session_id] = history
            logger.info(
                "[Memory] Cache miss for session %s — rebuilt %d messages from DB",
                session_id,
                len(history),
            )
        except Exception as exc:
            logger.warning("[Memory] DB reload failed for session %s: %s", session_id, exc)

        return history

    def persist_summary(
        self,
        session_id: str,
        task_id: str,
        mode: str,
        summary_text: str,
        message_count: int,
        db: Any,
    ) -> None:
        """
        Upsert the compressed summary to session_summaries.
        Called after every successful compression cycle.
        """
        try:
            from app.models.memory import SessionSummary

            token_estimate = len(summary_text.split())  # rough word-count proxy

            existing = (
                db.query(SessionSummary)
                .filter(
                    SessionSummary.task_id == task_id,
                    SessionSummary.mode == mode,
                )
                .first()
            )
            if existing:
                existing.summary_text = summary_text
                existing.message_count = message_count
                existing.token_estimate = token_estimate
            else:
                db.add(
                    SessionSummary(
                        id=str(uuid.uuid4()),
                        company_id="default",  # overridden by caller when possible
                        mode=mode,
                        task_id=task_id,
                        summary_text=summary_text,
                        message_count=message_count,
                        token_estimate=token_estimate,
                    )
                )
            db.commit()
            logger.info(
                "[Memory] Persisted summary for task=%s mode=%s (%d tokens est.)",
                task_id,
                mode,
                token_estimate,
            )
        except Exception as exc:
            logger.warning("[Memory] Failed to persist summary: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass


# Module-level singleton used by ai_chat.py
memory_store = MemoryStore()

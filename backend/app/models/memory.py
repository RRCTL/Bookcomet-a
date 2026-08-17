"""
Memory-related database models.

- SessionSummary: persists cross-session chat summaries per company+mode
- TokenUsageLog: tracks LLM token consumption for monitoring and UI display
"""
from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class SessionSummary(Base):
    """
    Persists progressive-summarization output so AI memory survives server
    restarts.  One row per (company_id, mode, task_id) — upserted on every
    compression cycle.
    """
    __tablename__ = "session_summaries"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    mode = Column(String, nullable=False, index=True)   # AR / AP / BANK / OTHER
    task_id = Column(
        String,
        ForeignKey("chat_tasks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    summary_text = Column(Text, nullable=False)
    message_count = Column(Integer, default=0)
    token_estimate = Column(Integer, default=0)         # rough token count of summary_text
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class TokenUsageLog(Base):
    """
    One row per LLM API call.  Used for backend monitoring and the frontend
    cost-indicator widget.
    """
    __tablename__ = "token_usage_log"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    task_id = Column(String, nullable=True, index=True)
    # call_type examples: ocr_enhance | ai_chat | summarize | gate_classify | title
    call_type = Column(String, nullable=False)
    model = Column(String, nullable=False)
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    # USD cost estimate (optional; calculated client-side based on model pricing)
    estimated_cost_usd = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

"""
Exclusion Rule model.

Per-company list of patterns that force a transaction row into
"needs manual review" rather than being auto-processed.

Pattern types:
  keyword  — matches anywhere in the row text (case-insensitive)
  vendor   — fuzzy-matched against payer/payee/vendor fields
  amount   — numeric threshold: flag when |amount| >= threshold value

Modes: optional comma-separated list of OCR modes to apply (AR, AP, BANK, OTHER).
       Empty = applies to ALL modes.
"""
import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database import Base


class ExclusionRule(Base):
    __tablename__ = "exclusion_rules"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, ForeignKey("companies.id"), nullable=False, index=True)
    pattern = Column(String, nullable=False)          # the keyword / vendor name / amount value
    pattern_type = Column(String, nullable=False, default="keyword")  # keyword | vendor | amount
    reason = Column(Text, nullable=True)              # user note shown in the review flag
    modes = Column(String, nullable=True)             # "AR,AP" or NULL for all modes
    is_active = Column(Boolean, nullable=False, default=True)
    hit_count = Column(Integer, nullable=False, default=0)
    last_hit_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

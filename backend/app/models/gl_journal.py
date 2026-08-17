"""General Ledger journal vouchers from RECON groups (v1: HKD, deterministic)."""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Column, String, Float, DateTime, Text, ForeignKey, Enum, Integer, Index
from sqlalchemy.sql import func

from app.database import Base


class GlJournalStatus(str, enum.Enum):
    DRAFT = "draft"
    POSTED = "posted"
    VOIDED = "voided"


class GlJournal(Base):
    __tablename__ = "gl_journals"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True, default="default")
    reconciliation_group_id = Column(String, ForeignKey("reconciliation_groups.id"), nullable=True, index=True)
    status = Column(
        Enum(GlJournalStatus, native_enum=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=GlJournalStatus.DRAFT,
    )
    journal_date = Column(DateTime, nullable=False)
    currency = Column(String, nullable=False, default="HKD")
    voucher_no = Column(String, nullable=False, index=True)
    narration = Column(Text, nullable=True)
    source = Column(String, nullable=False, default="recon_match")
    reversal_of_journal_id = Column(String, ForeignKey("gl_journals.id"), nullable=True, index=True)
    balancing_account_code = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    posted_at = Column(DateTime, nullable=True)
    posted_by = Column(String, nullable=True)

    __table_args__ = (
        Index("ix_gl_journal_company_group", "company_id", "reconciliation_group_id"),
    )


class GlJournalLine(Base):
    __tablename__ = "gl_journal_lines"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    journal_id = Column(String, ForeignKey("gl_journals.id"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    account_code = Column(String, nullable=False, index=True)
    debit = Column(Float, nullable=False, default=0.0)
    credit = Column(Float, nullable=False, default=0.0)
    memo = Column(Text, nullable=True)
    bank_txn_id = Column(String, nullable=True, index=True)
    ledger_txn_id = Column(String, nullable=True, index=True)

    __table_args__ = (
        Index("ix_gl_journal_line_journal", "journal_id", "line_no"),
    )

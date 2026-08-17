"""Draft-only journals from OCR rows (one journal per bank/ledger transaction)."""
from __future__ import annotations

import enum
import uuid

from sqlalchemy import Column, String, Float, DateTime, Text, ForeignKey, Enum, Integer, Index, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class OcrJournalStatus(str, enum.Enum):
    DRAFT = "draft"


class OcrJournal(Base):
    __tablename__ = "ocr_journals"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True, default="default")
    task_id = Column(String, ForeignKey("chat_tasks.id"), nullable=True, index=True)
    source = Column(String(8), nullable=False)  # bank | ledger
    source_txn_id = Column(String, nullable=False, index=True)
    status = Column(
        Enum(OcrJournalStatus, native_enum=False, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=OcrJournalStatus.DRAFT,
    )
    journal_date = Column(DateTime, nullable=False)
    currency = Column(String(8), nullable=False, default="HKD")
    voucher_no = Column(String, nullable=False, index=True)
    narration = Column(Text, nullable=True)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("company_id", "source", "source_txn_id", name="uq_ocr_journal_company_source_txn"),
        Index("ix_ocr_journal_company_task", "company_id", "task_id"),
    )


class OcrJournalLine(Base):
    __tablename__ = "ocr_journal_lines"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    journal_id = Column(String, ForeignKey("ocr_journals.id", ondelete="CASCADE"), nullable=False, index=True)
    line_no = Column(Integer, nullable=False)
    account_code = Column(String, nullable=False, index=True)
    debit = Column(Float, nullable=False, default=0.0)
    credit = Column(Float, nullable=False, default=0.0)
    memo = Column(Text, nullable=True)

    __table_args__ = (Index("ix_ocr_journal_line_journal", "journal_id", "line_no"),)

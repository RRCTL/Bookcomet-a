from sqlalchemy import Column, String, Float, DateTime, Text, Enum, Index
from sqlalchemy.sql import func
import enum

from app.database import Base


class TransactionStatus(str, enum.Enum):
    UNRECONCILED = "unreconciled"
    MATCHED = "matched"
    PARTIAL = "partial"   # Remainder from a partial multi-match; visible in matched table, still available for future matching
    EXCEPTION = "exception"


class BankTransaction(Base):
    __tablename__ = "bank_transactions"

    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False, index=True, default="default")
    account_id = Column(String, nullable=False, index=True)
    bank_date = Column(DateTime, nullable=False, index=True)
    amount = Column(Float, nullable=False, index=True)
    currency = Column(String, nullable=False, index=True)
    description_raw = Column(Text, nullable=False)
    description_norm = Column(Text)
    account_category = Column(String, nullable=True)
    reference = Column(String, index=True)
    import_batch_id = Column(String, index=True)
    status = Column(Enum(TransactionStatus), default=TransactionStatus.UNRECONCILED, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_bank_txn_match', 'currency', 'amount', 'bank_date'),
    )


class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"

    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False, index=True)
    module = Column(String, nullable=True, index=True)  # source module at transfer time: AP / AR
    doc_type = Column(String, nullable=False)  # invoice, cheque, journal
    doc_id = Column(String, index=True)
    book_date = Column(DateTime, nullable=False, index=True)
    amount = Column(Float, nullable=False, index=True)
    currency = Column(String, nullable=False, index=True)
    counterparty = Column(String)
    account_category = Column(String, nullable=True)
    reference = Column(String, index=True)
    import_batch_id = Column(String, index=True)
    # Explicit ledger side from AR/AP module ("Dr" | "Cr"); null = use GL heuristic
    dr_cr = Column(String, nullable=True)
    status = Column(Enum(TransactionStatus), default=TransactionStatus.UNRECONCILED, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_ledger_txn_match', 'currency', 'amount', 'book_date'),
    )

"""
Other-module database models (loans / fixed assets).

Hierarchy:
  OtherRecord  — task-scoped staging record (source of truth for UI)
      ├── LoanRecord          — synced formal loan data
      │       └── LoanInstallment   — per-installment schedule
      └── FixedAsset          — synced formal asset data
              └── AssetDepreciationSchedule — per-period depreciation
"""
from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.sql import func

from app.database import Base


class OtherRecord(Base):
    """
    Staging / source-of-truth record created when a document is routed from
    an AR/AP task to an OTHER task.  One row per extracted loan or asset.
    payload_json holds all structured fields; formal tables are auto-synced
    from it on every edit.
    """
    __tablename__ = "other_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    task_id = Column(
        String,
        ForeignKey("chat_tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    record_type = Column(String, nullable=False, index=True)  # loan | fixed_asset
    payload_json = Column(JSON, nullable=False, default=dict)

    # Traceability — link back to originating AR/AP task and file (no duplicate storage)
    source_task_id = Column(String, ForeignKey("chat_tasks.id"), nullable=True)
    source_file_id = Column(String, ForeignKey("task_files.id"), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class LoanRecord(Base):
    """
    Formal loan / liability record.  Auto-synced from OtherRecord
    whenever payload_json is saved.
    """
    __tablename__ = "loan_records"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    other_record_id = Column(
        String,
        ForeignKey("other_records.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    loan_reference = Column(String, nullable=True, index=True)
    lender_name = Column(String, nullable=False, default="Unknown")
    lender_account = Column(String, nullable=True)

    principal_amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String, nullable=False, default="HKD")
    interest_rate_pct = Column(Float, nullable=True)        # annual %
    tenor_months = Column(Integer, nullable=True)
    monthly_installment = Column(Float, nullable=True)

    start_date = Column(DateTime, nullable=True)
    maturity_date = Column(DateTime, nullable=True)
    first_payment_date = Column(DateTime, nullable=True)

    outstanding_principal = Column(Float, nullable=True)
    status = Column(String, default="active")               # active | repaid | default | closed

    memo = Column(Text, nullable=True)
    document_type = Column(String, nullable=True)           # loan_schedule | hp_agreement | mortgage

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class LoanInstallment(Base):
    """
    Per-installment row generated from LoanRecord terms.
    bank_txn_id_* is set when a bank transaction is matched to this installment.
    """
    __tablename__ = "loan_installments"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    loan_id = Column(
        String,
        ForeignKey("loan_records.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id = Column(String, nullable=False, index=True)

    installment_number = Column(Integer, nullable=False)
    due_date = Column(DateTime, nullable=False, index=True)

    principal_portion = Column(Float, nullable=False, default=0.0)
    interest_portion = Column(Float, nullable=False, default=0.0)
    total_payment = Column(Float, nullable=False, default=0.0)
    outstanding_principal_after = Column(Float, nullable=True)

    # RECON linkage — set when bank transactions are matched
    bank_txn_id_principal = Column(
        String, ForeignKey("bank_transactions.id"), nullable=True
    )
    bank_txn_id_interest = Column(
        String, ForeignKey("bank_transactions.id"), nullable=True
    )
    ledger_txn_id_interest = Column(
        String, ForeignKey("ledger_transactions.id"), nullable=True
    )

    status = Column(String, default="pending")  # pending | paid | overdue
    paid_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_loan_installment_due", "loan_id", "due_date"),
    )


class FixedAsset(Base):
    """
    Formal fixed asset record.  Auto-synced from OtherRecord.
    accumulated_depreciation and net_book_value are kept in sync by the
    depreciation engine whenever asset parameters change.
    """
    __tablename__ = "fixed_assets"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    other_record_id = Column(
        String,
        ForeignKey("other_records.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    asset_reference = Column(String, nullable=True, index=True)
    asset_name = Column(String, nullable=False, default="Unknown Asset")
    asset_type = Column(String, nullable=False, default="equipment")  # vehicle | property | equipment
    description = Column(Text, nullable=True)

    purchase_amount = Column(Float, nullable=False, default=0.0)
    acquisition_date = Column(DateTime, nullable=True)
    currency = Column(String, nullable=False, default="HKD")
    vendor = Column(String, nullable=True)
    invoice_ref = Column(String, nullable=True)

    useful_life_months = Column(Integer, nullable=False, default=60)
    residual_value = Column(Float, nullable=False, default=0.0)
    depreciation_method = Column(
        String, nullable=False, default="straight_line"
    )  # straight_line | declining_balance

    # Cached totals — updated by sync service after every depreciation recompute
    accumulated_depreciation = Column(Float, nullable=False, default=0.0)
    net_book_value = Column(Float, nullable=False, default=0.0)

    status = Column(String, default="active")  # active | disposed | fully_depreciated
    disposal_date = Column(DateTime, nullable=True)
    disposal_amount = Column(Float, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class AssetDepreciationSchedule(Base):
    """
    Period-by-period depreciation entries for a FixedAsset.
    Regenerated in full by the depreciation engine on any asset edit.
    """
    __tablename__ = "asset_depreciation_schedule"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id = Column(
        String,
        ForeignKey("fixed_assets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id = Column(String, nullable=False, index=True)

    period_start = Column(DateTime, nullable=False, index=True)
    period_end = Column(DateTime, nullable=False)
    period_type = Column(String, default="monthly")     # monthly | yearly

    depreciation_amount = Column(Float, nullable=False, default=0.0)
    accumulated_at_period_end = Column(Float, nullable=False, default=0.0)
    net_book_value_at_period_end = Column(Float, nullable=False, default=0.0)

    # Optional link to ledger transaction for full double-entry integration
    ledger_txn_id = Column(
        String, ForeignKey("ledger_transactions.id"), nullable=True
    )

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("idx_asset_depr_period", "asset_id", "period_start"),
    )

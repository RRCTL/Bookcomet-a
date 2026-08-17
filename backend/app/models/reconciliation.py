from sqlalchemy import Column, String, Float, DateTime, Text, Enum, ForeignKey, JSON, Boolean
from sqlalchemy.sql import func
import enum
import uuid

from app.database import Base


class MatchType(str, enum.Enum):
    RULE1 = "rule1"
    RULE2 = "rule2"
    RULE3 = "rule3"
    RULE4 = "rule4"
    RULE5 = "rule5"
    SPLIT = "split"
    MERGE = "merge"
    MANUAL = "manual"
    ONE_MANY = "one_many"
    MANY_ONE = "many_one"
    MANY_MANY = "many_many"


class DecisionType(str, enum.Enum):
    AUTO = "auto"
    MANUAL = "manual"
    REJECTED = "rejected"


class ReconciliationGroup(Base):
    """Groups multiple bank and/or ledger transactions into a single multi-match reconciliation."""
    __tablename__ = "reconciliation_groups"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True, default="default")
    trace_id = Column(String, nullable=True, index=True)
    match_cardinality = Column(String, nullable=False)  # "1:1", "1:N", "N:1", "N:N"
    total_bank_amount = Column(Float, nullable=False)
    total_ledger_amount = Column(Float, nullable=False)
    difference = Column(Float, nullable=False, default=0.0)  # total_bank - total_ledger
    partial_remainder_txn_id = Column(String, nullable=True)  # new BankTransaction created for remainder
    created_by = Column(String)
    created_at = Column(DateTime, server_default=func.now())


class ReconciliationMatch(Base):
    __tablename__ = "reconciliation_match"

    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False, index=True, default="default")
    trace_id = Column(String, nullable=True, index=True)
    # Nullable to support uneven multi-match groups (max(N,M) representative pairs)
    bank_txn_id = Column(String, ForeignKey("bank_transactions.id"), nullable=True)
    ledger_txn_id = Column(String, ForeignKey("ledger_transactions.id"), nullable=True)
    group_id = Column(String, ForeignKey("reconciliation_groups.id"), nullable=True, index=True)
    match_type = Column(Enum(MatchType), nullable=False)
    score = Column(Float, nullable=False)
    decision = Column(Enum(DecisionType), nullable=False)
    created_by = Column(String)
    created_at = Column(DateTime, server_default=func.now())


class ReconciliationAudit(Base):
    __tablename__ = "reconciliation_audit"

    id = Column(String, primary_key=True)
    company_id = Column(String, nullable=False, index=True, default="default")
    trace_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False)  # match, unmatch, override, flag
    payload_json = Column(JSON)
    user_id = Column(String)
    timestamp = Column(DateTime, server_default=func.now(), index=True)


class ReconSession(Base):
    """Tracks which transactions are in the user's active RECON unmatched pool.
    Replaced wholesale on every Match run; individual entries removed when a
    transaction is manually matched or removed from the group."""
    __tablename__ = "recon_session_transactions"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True)
    txn_id = Column(String, nullable=False, index=True)
    txn_type = Column(String, nullable=False)          # "bank" | "ledger"
    raw_txn_data = Column(JSON, nullable=True)          # raw txn object (id, amount, currency…)
    display_row = Column(JSON, nullable=True)           # SpreadsheetRow for checkbox table display
    created_at = Column(DateTime, server_default=func.now())


class ChartOfAccountEntry(Base):
    """User-editable Chart of Accounts entry. Defaults are seeded on first use."""
    __tablename__ = "chart_of_accounts"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id = Column(String, nullable=False, index=True, default="default")
    code = Column(String, nullable=False, index=True)
    name_en = Column(String, nullable=False)
    name_zh = Column(String, default="")
    category_type = Column(String, nullable=False)
    allowed_modes = Column(JSON, nullable=False)   # e.g. ["AR", "AP", "BANK"]
    is_default = Column(Boolean, default=False)    # True = seeded from built-in defaults
    opening_balance = Column(Float, nullable=True, default=None)          # Opening balance b/f amount
    opening_balance_dr_cr = Column(String, nullable=True, default=None)   # "Dr" | "Cr"
    created_at = Column(DateTime, server_default=func.now())

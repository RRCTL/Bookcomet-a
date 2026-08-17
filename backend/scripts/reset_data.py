"""
Reset all business data for a clean testing state.
Keeps user accounts and company records intact.

Usage (from the backend/ directory):
    python scripts/reset_data.py

Add --all to also wipe users, companies, and memberships:
    python scripts/reset_data.py --all
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import SessionLocal
from app.models import (
    AssetDepreciationSchedule,
    OtherRecord,
    AuditPackageArchive,
    BankTransaction,
    ChartOfAccountEntry,
    ChatTask,
    CompanyManual,
    CompanyManualVersion,
    CompanyProfile,
    CompanyRule,
    CompanyRuleAuditLog,
    CompanyRuleHitEvent,
    CompanyRuleMemory,
    CompanyRuleMemoryVersion,
    ExclusionRule,
    FixedAsset,
    LedgerTransaction,
    LoanInstallment,
    LoanRecord,
    Membership,
    OcrCompletionEvent,
    ReconciliationAudit,
    ReconciliationMatch,
    SessionSummary,
    TaskAuditLog,
    TaskFile,
    TaskMessage,
    TaskStateSnapshot,
    TokenUsageLog,
)
from app.models.auth_log import AuthAuditLog
from app.models.identity import Company, User


BUSINESS_DATA_TABLES = [
    # Reconciliation (children first)
    ReconciliationAudit,
    ReconciliationMatch,
    BankTransaction,
    LedgerTransaction,
    ChartOfAccountEntry,
    # Rules / memory
    CompanyRuleHitEvent,
    CompanyRuleAuditLog,
    CompanyRuleMemoryVersion,
    CompanyRuleMemory,
    CompanyManualVersion,
    CompanyManual,
    ExclusionRule,
    CompanyRule,
    CompanyProfile,
    # OCR / compliance
    OcrCompletionEvent,
    AuditPackageArchive,
    # Chat / AI
    TaskFile,
    TaskMessage,
    TaskStateSnapshot,
    TaskAuditLog,
    ChatTask,
    # Memory / token logs
    SessionSummary,
    TokenUsageLog,
    # Assets / loans
    AssetDepreciationSchedule,
    FixedAsset,
    LoanInstallment,
    LoanRecord,
    OtherRecord,
    # Auth audit log
    AuthAuditLog,
]

IDENTITY_TABLES = [
    Membership,
    Company,
    User,
]


def reset(wipe_users: bool = False) -> None:
    db = SessionLocal()
    try:
        tables = BUSINESS_DATA_TABLES + (IDENTITY_TABLES if wipe_users else [])
        total = 0
        for model in tables:
            count = db.query(model).delete()
            total += count
            print(f"  cleared {model.__tablename__:<40} ({count} rows)")

        db.commit()
        print(f"\nDone — {total} rows removed.")
        if wipe_users:
            print("Users, companies, and memberships have been wiped.")
            print("Re-register via the UI to create a fresh account.")
        else:
            print("User accounts and companies are intact.")
            print("Log in normally — the app will behave as if freshly onboarded.")
    except Exception as exc:
        db.rollback()
        print(f"\nError: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    wipe_all = "--all" in sys.argv

    if wipe_all:
        print("=== FULL RESET (users + all data) ===\n")
    else:
        print("=== BUSINESS DATA RESET (users preserved) ===\n")
        print("  Tip: pass --all to also wipe user accounts.\n")

    reset(wipe_users=wipe_all)

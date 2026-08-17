"""Seed test data for reconciliation testing"""
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from app.database import SessionLocal, engine, Base
from app.models import BankTransaction, LedgerTransaction, TransactionStatus

def seed_data():
    """Seed test transactions"""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    try:
        # Clear existing data
        db.query(BankTransaction).delete()
        db.query(LedgerTransaction).delete()
        
        # Bank transactions (from bank statement)
        bank_txns = [
            BankTransaction(
                id="BANK-001",
                account_id="ACC-001",
                bank_date=datetime(2026, 1, 15),
                amount=5000.00,
                currency="HKD",
                description_raw="CHQ 123456 - ABC Company",
                description_norm="chq 123456 abc company",
                reference="123456",
                import_batch_id="BATCH-001",
                status=TransactionStatus.UNRECONCILED
            ),
            BankTransaction(
                id="BANK-002",
                account_id="ACC-001",
                bank_date=datetime(2026, 1, 16),
                amount=3200.50,
                currency="HKD",
                description_raw="TRANSFER FROM XYZ LTD",
                description_norm="transfer from xyz ltd",
                reference="TRF-9876",
                import_batch_id="BATCH-001",
                status=TransactionStatus.UNRECONCILED
            ),
            BankTransaction(
                id="BANK-003",
                account_id="ACC-001",
                bank_date=datetime(2026, 1, 18),
                amount=1500.00,
                currency="HKD",
                description_raw="CHQ 789012 - Payment",
                description_norm="chq 789012 payment",
                reference="789012",
                import_batch_id="BATCH-001",
                status=TransactionStatus.UNRECONCILED
            ),
        ]
        
        # Ledger transactions (from accounting system)
        ledger_txns = [
            LedgerTransaction(
                id="LEDGER-001",
                company_id="COMP-001",
                doc_type="cheque",
                doc_id="CHQ-20260115-00001",
                book_date=datetime(2026, 1, 15),
                amount=5000.00,
                currency="HKD",
                counterparty="ABC Company",
                reference="123456",
                status=TransactionStatus.UNRECONCILED
            ),
            LedgerTransaction(
                id="LEDGER-002",
                company_id="COMP-001",
                doc_type="invoice",
                doc_id="INV-20260114-00050",
                book_date=datetime(2026, 1, 14),
                amount=3200.50,
                currency="HKD",
                counterparty="XYZ Limited",
                reference="INV-050",
                status=TransactionStatus.UNRECONCILED
            ),
            LedgerTransaction(
                id="LEDGER-003",
                company_id="COMP-001",
                doc_type="cheque",
                doc_id="CHQ-20260118-00002",
                book_date=datetime(2026, 1, 20),  # 2 days later
                amount=1500.00,
                currency="HKD",
                counterparty="Supplier Payment",
                reference="789012",
                status=TransactionStatus.UNRECONCILED
            ),
        ]
        
        db.add_all(bank_txns)
        db.add_all(ledger_txns)
        db.commit()
        
        print("Seed data created successfully!")
        print(f"   - {len(bank_txns)} bank transactions")
        print(f"   - {len(ledger_txns)} ledger transactions")
        
    except Exception as e:
        db.rollback()
        print(f"Error seeding data: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed_data()

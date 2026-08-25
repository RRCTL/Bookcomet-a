"""Deploy Codes → GL: resolve Books rows without db_id and rebuild module drafts."""
from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — register models
from app.database import Base
from app.models.gl_journal import GlJournalLine
from app.models.reconciliation import ChartOfAccountEntry
from app.models.transaction import LedgerTransaction, TransactionStatus
from app.services import gl_journal_service as glsvc
from app.services.job_tasks import (
    _build_coa_deploy_category_tuples,
    resolve_recon_txn_id_for_module_row,
)


def _seed_coa(db, company_id: str, codes: list[str]) -> None:
    for code in codes:
        db.add(
            ChartOfAccountEntry(
                id=str(uuid.uuid4()),
                company_id=company_id,
                code=code,
                name_en=f"Account {code}",
                name_zh="",
                category_type="expense" if code.startswith("5") else "asset",
                allowed_modes=["AR", "AP", "BANK"],
                is_default=False,
            )
        )
    db.commit()


class CoaDeployGlResolveTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.company_id = str(uuid.uuid4())
        _seed_coa(self.db, self.company_id, ["2100", "1999", "5060", "5020"])

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _ledger(self, *, voucher: str, amount: float, day: datetime) -> LedgerTransaction:
        lt = LedgerTransaction(
            id=str(uuid.uuid4()),
            company_id=self.company_id,
            module="AP",
            doc_type="ap",
            doc_id=voucher,
            book_date=day,
            amount=amount,
            currency="HKD",
            counterparty="Vendor",
            account_category=None,
            reference=voucher,
            import_batch_id="b1",
            status=TransactionStatus.UNRECONCILED,
            dr_cr="Dr",
        )
        self.db.add(lt)
        self.db.commit()
        return lt

    def test_resolve_by_natural_key_when_db_id_missing(self):
        lt = self._ledger(
            voucher="AP-7405464",
            amount=99.0,
            day=datetime(2024, 3, 27, tzinfo=timezone.utc),
        )
        module_row = {
            "id_number": "AP-7405464",
            "date": "27/03/2024",
            "debit": 99,
            "transaction_type": "AP",
            # intentionally no db_id / ledger_txn_id
        }
        resolved = resolve_recon_txn_id_for_module_row(
            self.db, self.company_id, module_row, mode="AP", is_bank=False
        )
        self.assertEqual(resolved, lt.id)

    def test_build_tuples_stamps_db_id_and_rebuild_updates_gl(self):
        lt = self._ledger(
            voucher="AP-00076/1",
            amount=117.0,
            day=datetime(2024, 2, 28, tzinfo=timezone.utc),
        )
        j = glsvc.ensure_draft_for_txn(self.db, self.company_id, ledger_txn_id=lt.id)
        before = {
            ln.account_code
            for ln in self.db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id)
        }
        self.assertIn("2100", before)

        txns = [
            {
                "id_number": "AP-00076/1",
                "date": "28/02/2024",
                "debit": 117,
                "transaction_type": "AP",
            }
        ]
        code_map = {"AP-00076/1": "5060"}
        tuples = _build_coa_deploy_category_tuples(
            self.db,
            self.company_id,
            txns,
            code_map,
            mode="AP",
            is_bank=False,
        )
        self.assertEqual(tuples, [("ledger", lt.id, "5060")])
        self.assertEqual(txns[0].get("db_id"), lt.id)
        self.assertEqual(txns[0].get("ledger_txn_id"), lt.id)

        glsvc.bulk_set_transaction_account_categories(self.db, self.company_id, tuples)
        rebuilt = glsvc.rebuild_module_approve_drafts_for_txns(
            self.db, self.company_id, set(), {lt.id}
        )
        self.assertEqual(rebuilt, [j.id])
        after = {
            ln.account_code
            for ln in self.db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id)
        }
        self.assertIn("5060", after)
        self.assertNotIn("2100", after)


if __name__ == "__main__":
    unittest.main()

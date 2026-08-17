"""Bank GL-only match (GL:1): draft cash+1999, approve creates AP/AR or bank."""
from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — register models
from app.database import Base
from app.models.gl_journal import GlJournal, GlJournalLine, GlJournalStatus
from app.models.reconciliation import ChartOfAccountEntry, ReconciliationGroup, ReconciliationMatch
from app.models.transaction import BankTransaction, LedgerTransaction, TransactionStatus
from app.services import gl_journal_service as glsvc
from app.services.reconciliation_service import ReconciliationEngine


def _seed_coa(db, company_id: str) -> None:
    specs = [
        ("1010", "asset", ["BANK"]),
        ("1020", "asset", ["BANK"]),
        ("1999", "asset", ["BANK", "AR", "AP"]),
        ("5000", "expense", ["AP"]),
        ("4000", "revenue", ["AR"]),
    ]
    for code, ct, modes in specs:
        db.add(
            ChartOfAccountEntry(
                id=str(uuid.uuid4()),
                company_id=company_id,
                code=code,
                name_en=f"Account {code}",
                name_zh="",
                category_type=ct,
                allowed_modes=modes,
                is_default=False,
            )
        )
    db.commit()


class GlOnlyMatchTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.company_id = str(uuid.uuid4())
        self.user_id = "test-user"
        _seed_coa(self.db, self.company_id)
        self.recon = ReconciliationEngine()

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _bank(self, *, amount: float, gl: str) -> BankTransaction:
        bt = BankTransaction(
            id=str(uuid.uuid4()),
            company_id=self.company_id,
            account_id="HSBC",
            bank_date=datetime(2026, 4, 11, tzinfo=timezone.utc),
            amount=amount,
            currency="HKD",
            description_raw="Fee",
            description_norm="fee",
            account_category=gl,
            reference="BR-GL",
            import_batch_id="b1",
            status=TransactionStatus.UNRECONCILED,
        )
        self.db.add(bt)
        self.db.commit()
        return bt

    def test_gl_only_match_creates_gl1_and_draft_cash_plus_suspense(self):
        bt = self._bank(amount=-88.0, gl="5000")
        result = asyncio.run(
            self.recon.gl_only_match([bt.id], self.company_id, self.user_id, "tr-1", self.db)
        )
        self.assertEqual(result["match_cardinality"], "GL:1")
        self.assertTrue(result["gl_only"])
        group = self.db.query(ReconciliationGroup).filter_by(id=result["group_id"]).one()
        self.assertEqual(group.match_cardinality, "GL:1")
        j = glsvc.ensure_draft_for_group(self.db, self.company_id, group.id)
        lines = (
            self.db.query(GlJournalLine)
            .filter(GlJournalLine.journal_id == j.id)
            .order_by(GlJournalLine.line_no)
            .all()
        )
        codes = {ln.account_code for ln in lines}
        self.assertIn("1010", codes)
        self.assertIn("1999", codes)
        self.assertNotIn("5000", codes)

    def test_approve_expense_creates_ap_and_replaces_suspense(self):
        bt = self._bank(amount=-50.0, gl="5000")
        result = asyncio.run(
            self.recon.gl_only_match([bt.id], self.company_id, self.user_id, "tr-2", self.db)
        )
        j = glsvc.ensure_draft_for_group(self.db, self.company_id, result["group_id"])
        posted = glsvc.post_journal(self.db, self.company_id, j.id, self.user_id)
        self.assertEqual(posted.status, GlJournalStatus.POSTED)
        lines = self.db.query(GlJournalLine).filter(GlJournalLine.journal_id == posted.id).all()
        codes = {ln.account_code for ln in lines}
        self.assertIn("1010", codes)
        self.assertIn("5000", codes)
        self.assertNotIn("1999", codes)
        ledgers = (
            self.db.query(LedgerTransaction)
            .filter(LedgerTransaction.company_id == self.company_id, LedgerTransaction.module == "AP")
            .all()
        )
        self.assertEqual(len(ledgers), 1)
        self.assertEqual(ledgers[0].account_category, "5000")
        group = self.db.query(ReconciliationGroup).filter_by(id=result["group_id"]).one()
        self.assertEqual(group.match_cardinality, "1:1")

    def test_approve_bank_coa_requires_confirm_then_creates_bank(self):
        bt = self._bank(amount=-120.0, gl="1020")
        result = asyncio.run(
            self.recon.gl_only_match([bt.id], self.company_id, self.user_id, "tr-3", self.db)
        )
        j = glsvc.ensure_draft_for_group(self.db, self.company_id, result["group_id"])
        with self.assertRaises(ValueError) as ctx:
            glsvc.post_journal(self.db, self.company_id, j.id, self.user_id)
        self.assertIn("CONFIRM_CREATE_BANK", str(ctx.exception))
        posted = glsvc.post_journal(
            self.db, self.company_id, j.id, self.user_id, confirm_bank_create=True
        )
        self.assertEqual(posted.status, GlJournalStatus.POSTED)
        banks = (
            self.db.query(BankTransaction)
            .filter(BankTransaction.company_id == self.company_id)
            .all()
        )
        self.assertEqual(len(banks), 2)
        counterpart = next(b for b in banks if b.id != bt.id)
        self.assertEqual(float(counterpart.amount), 120.0)
        self.assertEqual(counterpart.account_category, "1020")
        matches = (
            self.db.query(ReconciliationMatch)
            .filter(ReconciliationMatch.group_id == result["group_id"])
            .all()
        )
        self.assertEqual(len([m for m in matches if m.bank_txn_id]), 2)

    def test_empty_zero_draft_is_rebuilt_with_bank_amount(self):
        bt = self._bank(amount=18000.0, gl="1020")
        result = asyncio.run(
            self.recon.gl_only_match([bt.id], self.company_id, self.user_id, "tr-4", self.db)
        )
        gid = result["group_id"]
        j = glsvc.ensure_draft_for_group(self.db, self.company_id, gid)
        # Simulate corrupt empty draft (no lines / zero movement) that used to stick forever.
        self.db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id).delete(
            synchronize_session=False
        )
        self.db.commit()
        rebuilt = glsvc.ensure_draft_for_group(self.db, self.company_id, gid)
        lines = (
            self.db.query(GlJournalLine)
            .filter(GlJournalLine.journal_id == rebuilt.id)
            .order_by(GlJournalLine.line_no)
            .all()
        )
        self.assertGreaterEqual(len(lines), 2)
        td = sum(float(ln.debit or 0) for ln in lines)
        tc = sum(float(ln.credit or 0) for ln in lines)
        self.assertAlmostEqual(td, 18000.0, places=2)
        self.assertAlmostEqual(tc, 18000.0, places=2)
        codes = {ln.account_code for ln in lines}
        self.assertIn("1010", codes)
        self.assertIn("1999", codes)

    def test_gl1_uses_group_total_when_bank_amount_wiped(self):
        bt = self._bank(amount=18000.0, gl="5000")
        result = asyncio.run(
            self.recon.gl_only_match([bt.id], self.company_id, self.user_id, "tr-5", self.db)
        )
        bt.amount = 0.0
        self.db.commit()
        j = glsvc.rebuild_primary_draft_for_group(self.db, self.company_id, result["group_id"])
        lines = self.db.query(GlJournalLine).filter(GlJournalLine.journal_id == j.id).all()
        td = sum(float(ln.debit or 0) for ln in lines)
        self.assertAlmostEqual(td, 18000.0, places=2)

    def test_dissolve_orphan_group_with_no_match_rows(self):
        """Cancel must remove groups that only have totals (0 members) so refresh cannot resurrect them."""
        orphan = ReconciliationGroup(
            id=str(uuid.uuid4()),
            company_id=self.company_id,
            trace_id="tr-orphan",
            match_cardinality="GL:1",
            total_bank_amount=18000.0,
            total_ledger_amount=0.0,
            difference=18000.0,
            created_by=self.user_id,
        )
        self.db.add(orphan)
        self.db.commit()
        j = GlJournal(
            id=str(uuid.uuid4()),
            company_id=self.company_id,
            reconciliation_group_id=orphan.id,
            status=GlJournalStatus.DRAFT,
            journal_date=datetime(2026, 7, 30, tzinfo=timezone.utc),
            currency="HKD",
            voucher_no="GL-000094",
            narration="orphan",
            source=glsvc.SOURCE_RECON_MATCH,
        )
        self.db.add(j)
        self.db.commit()

        out = asyncio.run(
            self.recon.dissolve_group(
                orphan.id, self.company_id, self.user_id, "tr-d", "cancel_orphan", self.db
            )
        )
        self.assertTrue(out["group_dissolved"])
        self.assertIsNone(self.db.query(ReconciliationGroup).filter_by(id=orphan.id).first())
        self.assertIsNone(self.db.query(GlJournal).filter_by(id=j.id).first())


if __name__ == "__main__":
    unittest.main()

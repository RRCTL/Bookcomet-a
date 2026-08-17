"""Journal module v1: module drafts, match merge, post guards, manual journals."""
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
from app.models.reconciliation import ChartOfAccountEntry, ReconciliationGroup
from app.models.transaction import BankTransaction, LedgerTransaction, TransactionStatus
from app.services import gl_journal_service as glsvc
from app.services.reconciliation_service import ReconciliationEngine


def _seed_coa(db, company_id: str, codes: list[str]) -> None:
    for code in codes:
        db.add(
            ChartOfAccountEntry(
                id=str(uuid.uuid4()),
                company_id=company_id,
                code=code,
                name_en=f"Account {code}",
                name_zh="",
                category_type="asset",
                allowed_modes=["AR", "AP", "BANK"],
                is_default=False,
            )
        )
    db.commit()


class GlJournalModuleTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.company_id = str(uuid.uuid4())
        self.user_id = "test-user"
        _seed_coa(self.db, self.company_id, ["1010", "1100", "2100", "1999", "5000", "1000"])

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _ledger(self, *, amount: float, currency: str = "HKD", doc_type: str = "ap", module: str = "AP"):
        lt = LedgerTransaction(
            id=str(uuid.uuid4()),
            company_id=self.company_id,
            module=module,
            doc_type=doc_type,
            doc_id="V-1",
            book_date=datetime(2026, 4, 10, tzinfo=timezone.utc),
            amount=amount,
            currency=currency,
            counterparty="Vendor",
            account_category="2100",
            reference="AP-1",
            import_batch_id="b1",
            status=TransactionStatus.UNRECONCILED,
        )
        self.db.add(lt)
        self.db.commit()
        return lt

    def _bank(self, *, amount: float, currency: str = "HKD"):
        bt = BankTransaction(
            id=str(uuid.uuid4()),
            company_id=self.company_id,
            account_id="HSBC",
            bank_date=datetime(2026, 4, 11, tzinfo=timezone.utc),
            amount=amount,
            currency=currency,
            description_raw="Payment",
            description_norm="payment",
            account_category="1010",
            reference="BR-1",
            import_batch_id="b2",
            status=TransactionStatus.UNRECONCILED,
        )
        self.db.add(bt)
        self.db.commit()
        return bt

    def test_import_ledger_draft_post_rejected_while_unreconciled(self):
        lt = self._ledger(amount=100.0, currency="USD")
        j = glsvc.ensure_draft_for_txn(self.db, self.company_id, ledger_txn_id=lt.id)
        self.assertEqual(j.source, glsvc.SOURCE_MODULE_APPROVE)
        self.assertEqual(j.currency, "USD")
        self.assertEqual(j.status, GlJournalStatus.DRAFT)
        listed = glsvc.list_journals(self.db, self.company_id)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["recon_status"], "unreconciled")
        with self.assertRaises(ValueError) as ctx:
            glsvc.post_journal(self.db, self.company_id, j.id, self.user_id)
        self.assertIn("unreconciled", str(ctx.exception).lower())

    def test_match_merges_module_drafts_then_post_ok(self):
        # Same signed totals for match engine; AR receipt + bank deposit → Cr AR / Dr bank.
        # Note: doc_type "invoice" is treated as AP in gl_journal_service — use "receipt".
        lt = self._ledger(amount=100.0, doc_type="receipt", module="AR")
        lt.account_category = "1100"
        self.db.commit()
        bt = self._bank(amount=100.0)
        j_mod = glsvc.ensure_draft_for_txn(self.db, self.company_id, ledger_txn_id=lt.id)
        glsvc.ensure_draft_for_txn(self.db, self.company_id, bank_txn_id=bt.id)
        mod_id = j_mod.id

        engine = ReconciliationEngine()
        result = asyncio.run(
            engine.multi_manual_match(
                [bt.id],
                [lt.id],
                self.company_id,
                self.user_id,
                "trace-1",
                self.db,
            )
        )
        group_id = result["group_id"]
        gone = self.db.query(GlJournal).filter(GlJournal.id == mod_id).first()
        self.assertIsNone(gone)

        group_j = (
            self.db.query(GlJournal)
            .filter(
                GlJournal.company_id == self.company_id,
                GlJournal.reconciliation_group_id == group_id,
                GlJournal.status == GlJournalStatus.DRAFT,
            )
            .first()
        )
        self.assertIsNotNone(group_j)
        self.assertEqual(group_j.source, glsvc.SOURCE_RECON_MATCH)

        lines = self.db.query(GlJournalLine).filter(GlJournalLine.journal_id == group_j.id).all()
        for ln in lines:
            if ln.memo and "待選擇平衡科目" in (ln.memo or ""):
                self.fail("expected balanced match journal without suspense placeholder")
        self.assertEqual(len(lines), 2)
        by_code = {ln.account_code: ln for ln in lines}
        self.assertAlmostEqual(float(by_code["1010"].debit or 0), 100.0)
        self.assertAlmostEqual(float(by_code["1010"].credit or 0), 0.0)
        self.assertAlmostEqual(float(by_code["1100"].debit or 0), 0.0)
        self.assertAlmostEqual(float(by_code["1100"].credit or 0), 100.0)

        posted = glsvc.post_journal(self.db, self.company_id, group_j.id, self.user_id)
        self.assertEqual(posted.status, GlJournalStatus.POSTED)

    def test_match_ap_payment_clears_without_suspense(self):
        """Bank deposit + AP ledger: opposite Dr/Cr clear; no 1999 double-plug."""
        lt = self._ledger(amount=100.0, doc_type="payment", module="AP")
        bt = self._bank(amount=100.0)

        engine = ReconciliationEngine()
        result = asyncio.run(
            engine.multi_manual_match(
                [bt.id],
                [lt.id],
                self.company_id,
                self.user_id,
                "trace-ap-clear",
                self.db,
            )
        )
        group_id = result["group_id"]
        self.assertIsNone(result.get("partial_remainder_txn_id"))

        group_j = (
            self.db.query(GlJournal)
            .filter(
                GlJournal.company_id == self.company_id,
                GlJournal.reconciliation_group_id == group_id,
                GlJournal.status == GlJournalStatus.DRAFT,
            )
            .first()
        )
        self.assertIsNotNone(group_j)
        lines = self.db.query(GlJournalLine).filter(GlJournalLine.journal_id == group_j.id).all()
        for ln in lines:
            if ln.memo and "待選擇平衡科目" in (ln.memo or ""):
                self.fail("expected balanced match journal without suspense placeholder")
            self.assertNotEqual(ln.account_code, "1999")
        self.assertEqual(len(lines), 2)
        by_code = {ln.account_code: ln for ln in lines}
        self.assertAlmostEqual(float(by_code["1010"].debit or 0), 100.0)
        self.assertAlmostEqual(float(by_code["2100"].credit or 0), 100.0)

    def test_unequal_match_rejected_no_virtual_bank(self):
        lt = self._ledger(amount=90.0, doc_type="payment", module="AP")
        bt = self._bank(amount=100.0)
        bank_count_before = self.db.query(BankTransaction).filter(
            BankTransaction.company_id == self.company_id
        ).count()

        engine = ReconciliationEngine()
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(
                engine.multi_manual_match(
                    [bt.id],
                    [lt.id],
                    self.company_id,
                    self.user_id,
                    "trace-unequal",
                    self.db,
                )
            )
        msg = str(ctx.exception).lower()
        self.assertIn("do not match", msg)
        self.assertIn("virtual", msg)

        bank_count_after = self.db.query(BankTransaction).filter(
            BankTransaction.company_id == self.company_id
        ).count()
        self.assertEqual(bank_count_after, bank_count_before)
        self.assertEqual(
            self.db.query(ReconciliationGroup).filter(
                ReconciliationGroup.company_id == self.company_id
            ).count(),
            0,
        )
        self.db.refresh(bt)
        self.db.refresh(lt)
        self.assertEqual(bt.status, TransactionStatus.UNRECONCILED)
        self.assertEqual(lt.status, TransactionStatus.UNRECONCILED)

    def test_match_blocked_when_module_journal_posted(self):
        lt = self._ledger(amount=50.0)
        bt = self._bank(amount=50.0)
        j = glsvc.ensure_draft_for_txn(self.db, self.company_id, ledger_txn_id=lt.id)
        j.status = GlJournalStatus.POSTED
        j.posted_at = datetime.now(timezone.utc)
        self.db.commit()

        engine = ReconciliationEngine()
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(
                engine.multi_manual_match(
                    [bt.id],
                    [lt.id],
                    self.company_id,
                    self.user_id,
                    "trace-2",
                    self.db,
                )
            )
        self.assertIn("Unpost", str(ctx.exception))

    def test_manual_create_and_post_without_recon(self):
        j = glsvc.create_manual_journal(
            self.db,
            self.company_id,
            journal_date=datetime(2026, 5, 1, tzinfo=timezone.utc),
            currency="USD",
            narration="Adjust",
            voucher_no=None,
            lines=[
                {"account_code": "5000", "debit": 25.0, "credit": 0.0, "memo": "exp"},
                {"account_code": "1000", "debit": 0.0, "credit": 25.0, "memo": "cash"},
            ],
        )
        self.assertEqual(j.source, glsvc.SOURCE_MANUAL)
        self.assertEqual(j.currency, "USD")
        posted = glsvc.post_journal(self.db, self.company_id, j.id, self.user_id)
        self.assertEqual(posted.status, GlJournalStatus.POSTED)

    def test_mixed_currency_merge_rejected(self):
        lt = self._ledger(amount=100.0, currency="USD")
        bt = self._bank(amount=100.0, currency="HKD")
        glsvc.ensure_draft_for_txn(self.db, self.company_id, ledger_txn_id=lt.id)
        glsvc.ensure_draft_for_txn(self.db, self.company_id, bank_txn_id=bt.id)

        engine = ReconciliationEngine()
        with self.assertRaises(ValueError) as ctx:
            asyncio.run(
                engine.multi_manual_match(
                    [bt.id],
                    [lt.id],
                    self.company_id,
                    self.user_id,
                    "trace-3",
                    self.db,
                )
            )
        self.assertIn("currenc", str(ctx.exception).lower())

    def test_norm_currency_maps_hkd_aliases(self):
        self.assertEqual(glsvc._norm_currency("港元"), "HKD")
        self.assertEqual(glsvc._norm_currency("HK$"), "HKD")
        self.assertEqual(glsvc._norm_currency("usd"), "USD")

    def test_match_bank_credit_hkd_alias_to_ap(self):
        """Bank Cr (negative amount) + OCR 港元 must match AP ledger HKD same magnitude."""
        lt = self._ledger(amount=889.0, doc_type="payment", module="AP", currency="HKD")
        bt = self._bank(amount=-889.0, currency="港元")

        engine = ReconciliationEngine()
        result = asyncio.run(
            engine.multi_manual_match(
                [bt.id],
                [lt.id],
                self.company_id,
                self.user_id,
                "trace-hkd-alias-abs",
                self.db,
            )
        )
        self.assertTrue(result.get("group_id"))
        self.assertAlmostEqual(float(result.get("total_bank_amount") or 0), 889.0)
        self.assertAlmostEqual(float(result.get("total_ledger_amount") or 0), 889.0)
        self.assertAlmostEqual(float(result.get("difference") or 0), 0.0)

    def test_match_bank_debit_to_negative_ledger(self):
        """Bank Dr (+889) must match ledger stored as -889 (same magnitude)."""
        lt = self._ledger(amount=-889.0, doc_type="payment", module="AP", currency="HKD")
        bt = self._bank(amount=889.0, currency="HKD")

        engine = ReconciliationEngine()
        result = asyncio.run(
            engine.multi_manual_match(
                [bt.id],
                [lt.id],
                self.company_id,
                self.user_id,
                "trace-bank-pos-ledger-neg",
                self.db,
            )
        )
        self.assertTrue(result.get("group_id"))
        self.assertAlmostEqual(float(result.get("total_bank_amount") or 0), 889.0)
        self.assertAlmostEqual(float(result.get("total_ledger_amount") or 0), 889.0)
        self.assertAlmostEqual(float(result.get("difference") or 0), 0.0)

    def test_rule_amounts_match_ignores_sign(self):
        engine = ReconciliationEngine()
        self.assertTrue(engine._amounts_match(889.0, -889.0))
        self.assertTrue(engine._amounts_match(-889.0, 889.0))
        self.assertFalse(engine._amounts_match(889.0, 890.0))

    def test_sync_unreconciled_rewrites_account_amount_date(self):
        bt = self._bank(amount=100.0, currency="HKD")
        j = glsvc.ensure_draft_for_txn(self.db, self.company_id, bank_txn_id=bt.id)
        line = (
            self.db.query(GlJournalLine)
            .filter(GlJournalLine.journal_id == j.id, GlJournalLine.bank_txn_id == bt.id)
            .first()
        )
        self.assertIsNotNone(line)
        line.account_code = "1010"
        line.debit = 0.0
        line.credit = 55.0
        j.journal_date = datetime(2026, 6, 15, tzinfo=timezone.utc)
        self.db.commit()

        out = glsvc.sync_draft_journal_lines_to_transactions(self.db, self.company_id, j.id)
        self.assertTrue(out.get("module_fields_rewritten"))
        self.db.refresh(bt)
        self.assertEqual(bt.account_category, "1010")
        self.assertAlmostEqual(float(bt.amount), -55.0)
        self.assertEqual(bt.bank_date.date().isoformat(), "2026-06-15")

    def test_sync_reconciled_account_only_keeps_amount(self):
        lt = self._ledger(amount=100.0, doc_type="receipt", module="AR")
        bt = self._bank(amount=100.0)
        glsvc.ensure_draft_for_txn(self.db, self.company_id, ledger_txn_id=lt.id)
        glsvc.ensure_draft_for_txn(self.db, self.company_id, bank_txn_id=bt.id)
        engine = ReconciliationEngine()
        result = asyncio.run(
            engine.multi_manual_match(
                [bt.id],
                [lt.id],
                self.company_id,
                self.user_id,
                "trace-sync-locked",
                self.db,
            )
        )
        group_id = result["group_id"]
        group_j = (
            self.db.query(GlJournal)
            .filter(
                GlJournal.company_id == self.company_id,
                GlJournal.reconciliation_group_id == group_id,
                GlJournal.status == GlJournalStatus.DRAFT,
            )
            .first()
        )
        self.assertIsNotNone(group_j)
        bank_line = (
            self.db.query(GlJournalLine)
            .filter(GlJournalLine.journal_id == group_j.id, GlJournalLine.bank_txn_id == bt.id)
            .first()
        )
        self.assertIsNotNone(bank_line)
        bank_line.account_code = "1010"
        bank_line.debit = 999.0
        bank_line.credit = 0.0
        self.db.commit()
        before_amt = float(bt.amount)

        out = glsvc.sync_draft_journal_lines_to_transactions(self.db, self.company_id, group_j.id)
        self.assertFalse(out.get("module_fields_rewritten"))
        self.db.refresh(bt)
        self.assertEqual(bt.account_category, "1010")
        self.assertAlmostEqual(float(bt.amount), before_amt)


if __name__ == "__main__":
    unittest.main()

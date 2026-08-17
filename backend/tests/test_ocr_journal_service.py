import unittest
import uuid

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401 — register models
from app.database import Base
from app.services import ocr_journal_service as ojs


class OcrJournalServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        self.company_id = str(uuid.uuid4())

    def tearDown(self):
        self.db.close()
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def test_upsert_balanced_journal(self):
        j = ojs.upsert_journal(
            self.db,
            self.company_id,
            "bank",
            "bank-txn-1",
            task_id=None,
            journal_date="2026-04-10",
            narration="Test",
            voucher_no=None,
            currency="HKD",
            lines=[
                {"account_code": "1000", "debit": 100.0, "credit": 0.0},
                {"account_code": "2000", "debit": 0.0, "credit": 100.0},
            ],
        )
        self.db.commit()
        self.assertTrue(j.voucher_no.startswith("OCR-"))
        loaded = ojs.get_journal(self.db, self.company_id, "bank", "bank-txn-1")
        self.assertIsNotNone(loaded)
        d = ojs.journal_to_dict(self.db, loaded)
        self.assertEqual(len(d["lines"]), 2)

    def test_rejects_unbalanced(self):
        with self.assertRaises(ValueError):
            ojs.upsert_journal(
                self.db,
                self.company_id,
                "ledger",
                "led-1",
                task_id=None,
                journal_date="2026-04-10",
                narration=None,
                voucher_no=None,
                currency=None,
                lines=[
                    {"account_code": "1000", "debit": 50.0, "credit": 0.0},
                    {"account_code": "2000", "debit": 0.0, "credit": 100.0},
                ],
            )


if __name__ == "__main__":
    unittest.main()

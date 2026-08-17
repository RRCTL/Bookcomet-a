"""BOC 承前結餘 (Balance B/F) rows are kept in extracted transaction lists."""
from app.services.bank_statement_parser import BankStatementParser


def test_extract_keeps_boc_cheng_qian_row_when_filter_enabled():
    parser = BankStatementParser()
    ai = {
        "bank_id": "BOC",
        "transactions": [
            {
                "transaction_date": "2025-01-02",
                "value_date": "2025-01-02",
                "description": "承前結餘",
                "deposit": None,
                "withdrawal": None,
                "balance": 231881.87,
                "currency": "HKD",
                "account_type": "港元往來",
                "account_number": "000-000-0-000000-0",
            },
            {
                "transaction_date": "2025-01-03",
                "value_date": "2025-01-03",
                "description": "FPS",
                "deposit": 1000.0,
                "withdrawal": None,
                "balance": 232881.87,
                "currency": "HKD",
                "account_type": "港元往來",
                "account_number": "000-000-0-000000-0",
            },
        ],
    }
    out = parser._extract_transactions_from_ai_response(
        ai, filter_balance_anchor_rows=True
    )
    assert len(out) == 2
    assert "承前結餘" in str(out[0].get("備註") or out[0].get("description") or "")


def test_extract_still_strips_jin_qi_jie_yu_summary():
    parser = BankStatementParser()
    ai = {
        "transactions": [
            {
                "transaction_date": "2025-01-31",
                "description": "今期結餘",
                "deposit": None,
                "withdrawal": None,
                "balance": 50000.0,
                "currency": "HKD",
                "account_type": "港元往來",
            },
        ],
    }
    out = parser._extract_transactions_from_ai_response(
        ai, filter_balance_anchor_rows=True
    )
    assert len(out) == 0

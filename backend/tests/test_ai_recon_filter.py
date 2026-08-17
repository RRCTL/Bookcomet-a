"""Unit tests for AI match post-filter (equal amounts, real IDs only)."""

from app.services.ai_recon_service import filter_valid_matches


def test_filter_keeps_equal_amount_pair():
    bank = [{"id": "b1", "amount": 100.0}]
    ledger = [{"id": "l1", "amount": -100.0}]
    kept, dropped = filter_valid_matches(
        [{"bank_txn_id": "b1", "ledger_txn_id": "l1", "score": 0.9, "match_type": "1:1"}],
        bank,
        ledger,
    )
    assert dropped == 0
    assert len(kept) == 1
    assert kept[0]["bank_txn_id"] == "b1"
    assert kept[0]["ledger_txn_id"] == "l1"


def test_filter_drops_unequal_amounts():
    bank = [{"id": "b1", "amount": 48500.0}]
    ledger = [{"id": "l1", "amount": 1581619.0}]
    kept, dropped = filter_valid_matches(
        [{"bank_txn_id": "b1", "ledger_txn_id": "l1"}],
        bank,
        ledger,
    )
    assert kept == []
    assert dropped == 1


def test_filter_drops_unknown_ids_and_duplicates():
    bank = [{"id": "b1", "amount": 50.0}, {"id": "b2", "amount": 50.0}]
    ledger = [{"id": "l1", "amount": 50.0}]
    kept, dropped = filter_valid_matches(
        [
            {"bank_txn_id": "missing", "ledger_txn_id": "l1"},
            {"bank_txn_id": "b1", "ledger_txn_id": "l1"},
            {"bank_txn_id": "b2", "ledger_txn_id": "l1"},
        ],
        bank,
        ledger,
    )
    assert len(kept) == 1
    assert kept[0]["bank_txn_id"] == "b1"
    assert dropped == 2

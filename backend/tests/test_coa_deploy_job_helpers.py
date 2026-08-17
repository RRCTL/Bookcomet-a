from app.services.job_tasks import _apply_codes_to_rows, _results_to_code_map, _txn_dict_to_deploy


def test_results_to_code_map():
    rows = [
        {"id_number": "A1", "suggested_code": "5010", "confidence": 0.9},
        {"id_number": "A2", "suggested_code": None, "confidence": 0.1},
    ]
    assert _results_to_code_map(rows) == {"A1": "5010"}


def test_apply_codes_to_arap_rows_sets_category():
    rows = [{"id_number": "A1", "category": ""}]
    _apply_codes_to_rows(
        rows,
        {"A1": "5010"},
        is_bank=False,
        name_by_code={"5010": "Rent"},
    )
    assert rows[0]["account_code"] == "5010"
    assert rows[0]["category"] == "Rent"


def test_txn_dict_to_deploy_bank_uses_deposit_or_withdrawal():
    dep = _txn_dict_to_deploy(
        {"id_number": "B1", "date": "2024-01-01", "deposit": 100, "particulars": "In"},
        is_bank=True,
        default_mode="BANK",
    )
    assert dep.transaction_type == "BANK"
    assert dep.amount == 100

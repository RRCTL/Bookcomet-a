"""BEA-like activity table mapping and field repair (fictional fixtures only)."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.bank_statement_parser import BankStatementParser
from app.services.statement_activity_map import (
    align_cells_to_headers,
    bank_row_has_placement_issues,
    bank_row_placement_issues,
    map_activity_table_rows,
    parse_statement_date,
    repair_shifted_bank_fields,
)


BEA_HEADERS = [
    "Cur",
    "Date",
    "Bk Ref",
    "Transaction Details",
    "Deposit",
    "Withdrawal",
    "Balance",
]


def test_parse_ddmmmyy_and_iso():
    assert parse_statement_date("05AUG20") == "2020-08-05"
    assert parse_statement_date("11AUG20") == "2020-08-11"
    assert parse_statement_date("31JUL20") == "2020-07-31"
    assert parse_statement_date("2020-08-11") == "2020-08-11"
    assert parse_statement_date("03/08/2020") == "2020-08-03"
    assert parse_statement_date("") == ""
    assert parse_statement_date("not-a-date") == ""


def test_align_inserts_blank_cur_when_date_shifted_left():
    # OCR dropped blank Cur; first cell is the date.
    aligned = align_cells_to_headers(
        BEA_HEADERS,
        ["03AUG20", "1005", "FPS TRANS WDL", "", "1,250.00", "8,750.00"],
    )
    assert aligned[0] == ""
    assert aligned[1] == "03AUG20"
    assert aligned[2] == "1005"
    assert aligned[5] == "1,250.00"
    assert aligned[6] == "8,750.00"


def test_map_bea_like_table_blank_cur_ddmmmyy_wrap_two_sections_total():
    """Fictional BEA ACCOUNT ACTIVITIES grid — no real customer data."""
    rows = [
        # Section 1 opening
        ["HKD", "31JUL20", "", "Balance B/F 承上結餘", "", "", "10,000.00"],
        ["", "03AUG20", "1005", "FPS TRANS WDL 轉數快轉賬提款", "", "1,250.00", "8,750.00"],
        # Continuation
        ["", "", "", "TO FPS ID 1234567 INTERNET FRN20200803000000001", "", "", ""],
        ["", "07AUG20", "0000", "CHEQUE 000101 支票支出", "", "300.00", "8,450.00"],
        # Continuation that is only a value-date-looking label
        ["", "", "", "06AUG20", "", "", ""],
        ["", "12AUG20", "9995", "CHEQUE DEPOSIT 支票存款", "2,000.00", "", "10,450.00"],
        ["", "", "", "Total Transaction Amount 交易總金額", "2,000.00", "1,550.00", ""],
        # Section 2 opening + one txn (blank Cur again)
        ["HKD", "31JUL20", "", "Balance B/F 承上結餘", "", "", "5,000.00"],
        ["", "11AUG20", "2001", "INTEREST CREDIT 利息", "12.50", "", "5,012.50"],
        ["", "", "", "Total Transaction Amount 交易總金額", "12.50", "0.00", ""],
    ]

    mapped = map_activity_table_rows(BEA_HEADERS, rows)
    # Opening + 3 txns in section 1 + opening + 1 txn in section 2 = 6 (Total rows skipped)
    assert len(mapped) == 6

    bf1 = mapped[0]
    assert bf1["date"] == "2020-07-31"
    assert bf1["currency"] == "HKD"
    assert bf1["deposit"] is None
    assert bf1["withdrawal"] is None
    assert bf1["balance"] == 10000.0
    assert "Balance B/F" in bf1["description"] or "承上結餘" in bf1["description"]

    fps = mapped[1]
    assert fps["date"] == "2020-08-03"
    assert fps["currency"] == "HKD"  # carried from B/F
    assert fps["withdrawal"] == 1250.0
    assert fps["deposit"] is None
    assert fps["balance"] == 8750.0
    assert "1005" in fps["description"]
    assert "TO FPS ID 1234567" in fps["description"]
    assert "FRN20200803000000001" in fps["description"]

    cheque = mapped[2]
    assert cheque["date"] == "2020-08-07"
    assert cheque["withdrawal"] == 300.0
    assert "06AUG20" in cheque["description"]

    deposit = mapped[3]
    assert deposit["date"] == "2020-08-12"
    assert deposit["deposit"] == 2000.0
    assert deposit["withdrawal"] is None
    assert deposit["balance"] == 10450.0

    bf2 = mapped[4]
    assert bf2["balance"] == 5000.0
    assert bf2["currency"] == "HKD"

    interest = mapped[5]
    assert interest["date"] == "2020-08-11"
    assert interest["deposit"] == 12.5
    assert interest["currency"] == "HKD"

    # No Total rows leaked
    assert all("Total Transaction" not in (r["description"] or "") for r in mapped)
    assert all("交易總金額" not in (r["description"] or "") for r in mapped)


def test_map_handles_ocr_shifted_blank_cur_rows():
    """When Cur is blank and cells shift left, Date must not become currency."""
    rows = [
        ["HKD", "31JUL20", "", "Balance B/F 承上結餘", "", "", "10,000.00"],
        # Dropped blank Cur → date in first cell
        ["03AUG20", "1005", "FPS TRANS WDL", "", "1,250.00", "8,750.00"],
    ]
    mapped = map_activity_table_rows(BEA_HEADERS, rows)
    assert len(mapped) == 2
    txn = mapped[1]
    assert txn["date"] == "2020-08-03"
    assert txn["currency"] == "HKD"
    assert txn["withdrawal"] == 1250.0
    assert txn["balance"] == 8750.0
    assert not bank_row_has_placement_issues(txn)


def test_repair_shifted_currency_holds_iso_date():
    txn = {
        "date": "",
        "日期": "",
        "currency": "2020-08-11",
        "幣別": "2020-08-11",
        "deposit": None,
        "withdrawal": 4600.0,
        "balance": None,
        "description": "SAMPLE TRANSFER",
        "存入": "",
        "提取": "4600.00",
        "原幣結餘": "",
    }
    repair_shifted_bank_fields(txn)
    assert txn["date"] == "2020-08-11"
    assert txn["日期"] == "2020-08-11"
    assert txn["currency"] == ""
    assert txn["幣別"] == ""


def test_repair_shifted_currency_holds_date_and_amount_string():
    txn = {
        "date": "",
        "currency": "2020-08-11 4,600.00",
        "幣別": "2020-08-11 4,600.00",
        "deposit": None,
        "withdrawal": None,
        "description": "SAMPLE",
    }
    repair_shifted_bank_fields(txn)
    assert txn["date"] == "2020-08-11"
    assert txn["currency"] == ""
    assert txn.get("withdrawal") == 4600.0 or txn.get("提取") == "4600.00"


def test_normalize_transaction_repairs_bea_shift():
    raw = {
        "transaction_date": "",
        "currency": "11AUG20",
        "description": "FPS TRANS WDL fictional",
        "deposit": None,
        "withdrawal": 1250.0,
        "balance": 8750.0,
    }
    out = BankStatementParser._normalize_transaction(raw)
    assert out["date"] == "2020-08-11"
    assert out["日期"] == "2020-08-11"
    # Invalid currency cleared then defaulted to HKD
    assert out["currency"] == "HKD"
    assert out["幣別"] == "HKD"
    assert "1250" in str(out.get("提取") or out.get("spent") or "")


def test_placement_issues_flag_empty_date_and_bad_currency():
    bad = {
        "date": "",
        "currency": "2020-08-11",
        "deposit": 100,
        "withdrawal": None,
        "balance": 900,
        "description": "x",
    }
    issues = bank_row_placement_issues(bad)
    assert "bank_date_missing_or_unparseable" in issues
    assert "bank_currency_misplaced" in issues

    good = {
        "date": "2020-08-11",
        "currency": "HKD",
        "deposit": None,
        "withdrawal": 100.0,
        "balance": 900.0,
        "description": "x",
    }
    assert bank_row_placement_issues(good) == []


def _resume_db_with_bank_file(task_id: str = "task-1", task_file_id: str = "tf-bea"):
    """Minimal db mock so resume ownership checks see one owned run file."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    task = SimpleNamespace(id=task_id)
    run_file = SimpleNamespace(
        task_file_id=task_file_id,
        original_filename="Fictional-BEA.pdf",
        run_id="run-bea",
    )
    task_file = SimpleNamespace(id=task_file_id, original_filename="Fictional-BEA.pdf")
    db = MagicMock()
    db.commit = MagicMock()
    db.refresh = MagicMock()

    class _Chain:
        def __init__(self, rows):
            self._rows = rows

        def filter(self, *_a, **_k):
            return self

        def all(self):
            return self._rows

        def first(self):
            return self._rows[0] if self._rows else None

    def query_side_effect(model):
        name = getattr(model, "__name__", str(model))
        if name == "ChatTask":
            return _Chain([task])
        if name == "WorkflowRunFile":
            return _Chain([run_file])
        if name == "TaskFile":
            return _Chain([task_file])
        return _Chain([])

    db.query.side_effect = query_side_effect
    return db


@pytest.mark.asyncio
async def test_resume_rejects_bank_rows_with_placement_issues():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.graph.workflow_service import WorkflowService

    run = SimpleNamespace(
        id="run-bea-1",
        task_id="task-1",
        company_id="co-1",
        processing_mode="BANK",
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
    )
    db = _resume_db_with_bank_file()

    payload = {
        "bankTransactions": [
            {
                "source_file": "Fictional-BEA.pdf P1",
                "date": "",
                "currency": "2020-08-11",
                "deposit": None,
                "withdrawal": 4600,
                "balance": None,
                "description": "fictional shifted row",
                "company_currency": "HKD",
                "company_amount": 4600,
                "exchange_rate": 1,
            }
        ]
    }

    with patch("app.graph.executor.run_workflow_graph", new_callable=AsyncMock):
        with pytest.raises(HTTPException) as ei:
            await WorkflowService.resume_run(
                db,
                run,
                approved_payload=payload,
                skip_coa=False,
                user_id="user-1",
            )
    assert ei.value.status_code == 400
    assert "need checking" in str(ei.value.detail).lower() or "misplaced" in str(ei.value.detail).lower()


@pytest.mark.asyncio
async def test_resume_accepts_repaired_bank_rows():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.graph.workflow_service import WorkflowService

    run = SimpleNamespace(
        id="run-bea-2",
        task_id="task-1",
        company_id="co-1",
        processing_mode="BANK",
        run_status="awaiting_review",
        node_states_json={},
        console_log_json=[],
    )
    db = _resume_db_with_bank_file()

    payload = {
        "bankTransactions": [
            {
                "source_file": "Fictional-BEA.pdf P1",
                "date": "2020-08-11",
                "currency": "HKD",
                "deposit": None,
                "withdrawal": 1250.0,
                "balance": 8750.0,
                "description": "fictional ok row",
                "company_currency": "HKD",
                "company_amount": 1250.0,
                "exchange_rate": 1,
            }
        ]
    }

    with patch(
        "app.graph.executor.run_workflow_graph", new_callable=AsyncMock
    ) as mock_graph:
        with patch.object(
            WorkflowService, "_transfer_to_module", new_callable=AsyncMock
        ):
            result = await WorkflowService.resume_run(
                db,
                run,
                approved_payload=payload,
                skip_coa=False,
                user_id="user-1",
            )
    assert result is run
    mock_graph.assert_awaited_once()

"""Unit tests for workflow OCR row merge (Run + Re-VLM table payload source)."""

from types import SimpleNamespace

from app.graph.workflow_service import (
    _apply_vlm_ocr_states,
    _merge_run_files_ocr,
    _ocr_by_file_from_run_files,
    rows_from_ocr_payload,
)
from app.models.workflow import WorkflowRun


def test_merge_ar_single_page_ai_enhanced_tsv_rows():
    payload = {
        "ai_enhanced": {
            "tsv_rows": [
                {"voucher_no": "AR-001", "amount": "100", "date": "2022-01-08"},
            ],
        },
    }
    rows = rows_from_ocr_payload(payload)
    assert len(rows) == 1
    assert rows[0]["voucher_no"] == "AR-001"


def test_merge_ap_multi_page_ai_enhanced_tsv_rows():
    payload = {
        "pages": [
            {
                "page": 1,
                "ai_enhanced": {
                    "tsv_rows": [{"voucher_no": "P1", "amount": "10"}],
                },
            },
            {
                "page": 2,
                "ai_enhanced": {
                    "tsv_rows": [{"voucher_no": "P2", "amount": "20"}],
                },
            },
        ],
    }
    rows = rows_from_ocr_payload(payload)
    assert len(rows) == 2
    assert {r["voucher_no"] for r in rows} == {"P1", "P2"}


def test_merge_multi_page_stamps_page_number():
    payload = {
        "pages": [
            {
                "page": 1,
                "ai_enhanced": {
                    "tsv_rows": [{"憑證號": "A", "存入": "100"}],
                },
            },
            {
                "page": 3,
                "ai_enhanced": {
                    "transactions": [{"date": "2022-01-02", "deposit": "50"}],
                },
            },
        ],
    }
    rows = rows_from_ocr_payload(payload)
    assert len(rows) == 2
    assert rows[0]["_page"] == 1
    assert rows[1]["_page"] == 3


def test_merge_bank_top_level_transactions():
    payload = {
        "transactions": [
            {"日期": "2022-01-01", "存入": "500", "提取": "", "原幣結餘": "1500"},
            {"date": "2022-01-02", "deposit": "100", "withdrawal": "50", "balance": "1550"},
        ],
    }
    rows = rows_from_ocr_payload(payload)
    assert len(rows) == 2


def test_merge_bank_ai_enhanced_transactions():
    payload = {
        "ai_enhanced": {
            "transactions": [{"date": "2022-03-01", "deposit": "200"}],
        },
    }
    rows = rows_from_ocr_payload(payload)
    assert len(rows) == 1
    assert rows[0]["deposit"] == "200"


def test_merge_other_legacy_ai_enhanced_rows():
    payload = {
        "ai_enhanced": {
            "rows": [{"voucher_no": "AL-1", "amount": "999", "category": "Fixed asset"}],
        },
    }
    rows = rows_from_ocr_payload(payload)
    assert len(rows) == 1
    assert rows[0]["category"] == "Fixed asset"


def test_merge_top_level_tsv_rows():
    payload = {"tsv_rows": [{"voucher_no": "TOP", "amount": "1"}]}
    rows = rows_from_ocr_payload(payload)
    assert len(rows) == 1
    assert rows[0]["voucher_no"] == "TOP"


def test_merge_empty_payload():
    assert rows_from_ocr_payload({}) == []


def test_merge_run_files_skips_non_ok_status():
    ok_file = SimpleNamespace(
        file_status="ok",
        task_file_id="file-a",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "OK"}]}},
    )
    warn_file = SimpleNamespace(
        file_status="warning",
        task_file_id="file-b",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "WARN"}]}},
    )
    rows = _merge_run_files_ocr([ok_file, warn_file])
    assert len(rows) == 1
    assert rows[0]["voucher_no"] == "OK"


def test_apply_vlm_ocr_states_keeps_per_file_ocr_on_merge_path():
    f1 = SimpleNamespace(
        file_status="ok",
        task_file_id="batch1-file",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "B1"}]}},
    )
    f2 = SimpleNamespace(
        file_status="ok",
        task_file_id="batch2-file",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "B2"}]}},
    )
    run = WorkflowRun(
        id="run-1",
        company_id="co-1",
        task_id="task-1",
        owner_user_id="user-1",
        processing_mode="AP",
        graph_json={},
        node_states_json={"table_source": "merge"},
    )
    manager_rows = [{"voucher_no": "B2-MGR"}]
    _apply_vlm_ocr_states(run, [f1, f2], manager_rows)
    states = run.node_states_json
    assert states["merged_ocr"] == manager_rows
    assert set(states["ocr_by_file"].keys()) == {"batch1-file", "batch2-file"}
    assert states["ocr_by_file"]["batch1-file"][0]["voucher_no"] == "B1"


def test_ocr_by_file_keys_task_file_id():
    f1 = SimpleNamespace(
        file_status="ok",
        task_file_id="id-1",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "A"}]}},
    )
    f2 = SimpleNamespace(
        file_status="ok",
        task_file_id="id-2",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "B"}]}},
    )
    pending = SimpleNamespace(
        file_status="pending",
        task_file_id="id-3",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "C"}]}},
    )
    by_file = _ocr_by_file_from_run_files([f1, f2, pending])
    assert set(by_file.keys()) == {"id-1", "id-2"}
    assert by_file["id-1"][0]["voucher_no"] == "A"
    assert by_file["id-2"][0]["voucher_no"] == "B"

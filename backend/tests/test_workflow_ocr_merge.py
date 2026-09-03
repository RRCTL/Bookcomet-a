"""Unit tests for workflow OCR row merge (Run + Re-VLM table payload source)."""

from types import SimpleNamespace

from app.graph.ocr_partial_merge import upsert_ocr_pages
from app.graph.workflow_service import (
    _apply_vlm_ocr_states,
    _merge_run_files_ocr,
    _ocr_by_file_from_run_files,
    apply_partial_ocr_to_running_file,
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


def test_merge_run_files_skips_pending_and_failed():
    ok_file = SimpleNamespace(
        file_status="ok",
        task_file_id="file-a",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "OK"}]}},
    )
    running_file = SimpleNamespace(
        file_status="running",
        task_file_id="file-r",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "LIVE"}]}},
    )
    warn_file = SimpleNamespace(
        file_status="warning",
        task_file_id="file-b",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "WARN"}]}},
    )
    pending = SimpleNamespace(
        file_status="pending",
        task_file_id="file-p",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "PEND"}]}},
    )
    failed = SimpleNamespace(
        file_status="failed",
        task_file_id="file-f",
        result_summary_json={"ai_enhanced": {"tsv_rows": [{"voucher_no": "FAIL"}]}},
    )
    rows = _merge_run_files_ocr([ok_file, running_file, warn_file, pending, failed])
    assert {r["voucher_no"] for r in rows} == {"OK", "LIVE", "WARN"}


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
    running = SimpleNamespace(
        file_status="running",
        task_file_id="id-4",
        result_summary_json={
            "pages": [
                {"page": 1, "ai_enhanced": {"tsv_rows": [{"voucher_no": "P1"}]}},
            ],
        },
    )
    by_file = _ocr_by_file_from_run_files([f1, f2, pending, running])
    assert set(by_file.keys()) == {"id-1", "id-2", "id-4"}
    assert by_file["id-1"][0]["voucher_no"] == "A"
    assert by_file["id-2"][0]["voucher_no"] == "B"
    assert by_file["id-4"][0]["voucher_no"] == "P1"


def test_apply_partial_ocr_to_running_file_streams_pages():
    running = SimpleNamespace(
        file_status="running",
        task_file_id="file-live",
        result_summary_json=None,
    )
    run = WorkflowRun(
        id="run-live",
        company_id="co-1",
        task_id="task-1",
        owner_user_id="user-1",
        processing_mode="AP",
        graph_json={},
        node_states_json={},
    )
    apply_partial_ocr_to_running_file(
        run,
        [running],
        running,
        {
            "pages": [
                {"page": 1, "ai_enhanced": {"tsv_rows": [{"voucher_no": "LIVE-1"}]}},
                {"page": 2, "ai_enhanced": {"tsv_rows": [{"voucher_no": "LIVE-2"}]}},
            ],
        },
    )
    assert running.result_summary_json["pages"][0]["page"] == 1
    assert [r["voucher_no"] for r in run.node_states_json["ocr_by_file"]["file-live"]] == [
        "LIVE-1",
        "LIVE-2",
    ]


def test_rows_from_ocr_payload_includes_timeout_stub():
    payload = {
        "pages": [
            {
                "page": 6,
                "receipt_index": 2,
                "receipt_instance_id": "p6-r02",
                "status": "error",
                "error_code": "VLM_CROP_TIMEOUT",
                "ai_enhanced": {
                    "tsv_rows": [
                        {
                            "voucher_no": "P6-R2",
                            "amount": "",
                            "memo": "[OCR timeout]",
                            "needs_review": True,
                            "validation_flags": ["ocr_timeout"],
                        }
                    ]
                },
            }
        ]
    }
    rows = rows_from_ocr_payload(payload)
    assert len(rows) == 1
    assert rows[0]["voucher_no"] == "P6-R2"
    assert rows[0]["memo"] == "[OCR timeout]"
    assert "ocr_timeout" in rows[0]["validation_flags"]


def test_upsert_ocr_pages_keeps_earlier_page_when_later_crop_arrives():
    existing = [
        {
            "page": 1,
            "receipt_index": 1,
            "receipt_instance_id": "p1-r01",
            "ai_enhanced": {"tsv_rows": [{"voucher_no": "C1"}]},
        }
    ]
    incoming = [
        {
            "page": 6,
            "receipt_index": 2,
            "receipt_instance_id": "p6-r02",
            "status": "error",
            "ai_enhanced": {"tsv_rows": [{"voucher_no": "P6-R2", "memo": "[OCR timeout]"}]},
        }
    ]
    merged = upsert_ocr_pages(existing, incoming)
    assert [p["receipt_instance_id"] for p in merged] == ["p1-r01", "p6-r02"]


def test_upsert_ocr_pages_replaces_stub_for_same_instance():
    existing = [
        {
            "page": 6,
            "receipt_index": 2,
            "receipt_instance_id": "p6-r02",
            "status": "error",
            "ai_enhanced": {"tsv_rows": [{"voucher_no": "P6-R2"}]},
        }
    ]
    incoming = [
        {
            "page": 6,
            "receipt_index": 2,
            "receipt_instance_id": "p6-r02",
            "status": "success",
            "ai_enhanced": {"tsv_rows": [{"voucher_no": "C2"}]},
        }
    ]
    merged = upsert_ocr_pages(existing, incoming)
    assert len(merged) == 1
    assert merged[0]["status"] == "success"
    assert merged[0]["ai_enhanced"]["tsv_rows"][0]["voucher_no"] == "C2"


def test_apply_partial_ocr_upserts_and_keeps_total_pages():
    running = SimpleNamespace(
        file_status="running",
        task_file_id="file-live",
        result_summary_json={
            "document_type": "multi_page_pdf",
            "total_pages": 6,
            "pages": [
                {
                    "page": 1,
                    "receipt_index": 1,
                    "receipt_instance_id": "p1-r01",
                    "ai_enhanced": {"tsv_rows": [{"voucher_no": "C1"}]},
                }
            ],
        },
    )
    run = WorkflowRun(
        id="run-live",
        company_id="co-1",
        task_id="task-1",
        owner_user_id="user-1",
        processing_mode="AP",
        graph_json={},
        node_states_json={},
    )
    apply_partial_ocr_to_running_file(
        run,
        [running],
        running,
        {
            "pages": [
                {
                    "page": 6,
                    "receipt_index": 2,
                    "receipt_instance_id": "p6-r02",
                    "status": "error",
                    "ai_enhanced": {
                        "tsv_rows": [{"voucher_no": "P6-R2", "memo": "[OCR timeout]"}]
                    },
                }
            ],
        },
    )
    assert running.result_summary_json["total_pages"] == 6
    ids = [p["receipt_instance_id"] for p in running.result_summary_json["pages"]]
    assert ids == ["p1-r01", "p6-r02"]
    assert {r["voucher_no"] for r in run.node_states_json["ocr_by_file"]["file-live"]} == {
        "C1",
        "P6-R2",
    }

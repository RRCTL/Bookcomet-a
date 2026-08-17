from types import SimpleNamespace

from app.graph.workflow_service import _finalize_run_after_vlm


def test_finalize_merge_path_uses_merged_rows_not_file_ocr():
    run = SimpleNamespace(
        node_states_json={"table_source": "merge"},
        run_status="executing",
        console_log_json=[],
    )
    run_files = [
        SimpleNamespace(
            task_file_id="file-1",
            file_status="ok",
            result_summary_json={"ai_enhanced": {"tsv_rows": [{"vendor": "stale"}]}},
        )
    ]
    merge_rows = [{"vendor": "winner", "memo": "winner", "total": "100", "amount": "100", "payee": "winner", "date": "2024-01-01"}]

    _finalize_run_after_vlm(
        run,
        run_files,
        ok_count=1,
        warn_count=0,
        console_message="Merge finished.",
        vlm_node_id="merge",
        merged_rows=merge_rows,
    )

    assert run.run_status == "awaiting_review"
    assert run.node_states_json["merged_ocr"] == merge_rows
    assert run.node_states_json["ocr_by_file"] == {"file-1": merge_rows}
    merge_detail = run.node_states_json["merge"]["detail"]
    assert merge_detail["row_count"] == 1
    assert merge_detail.get("feedback") == "winner"
    assert merge_detail.get("ok") == 1


def test_finalize_run_after_vlm_populates_rich_vlm_detail():
    run = SimpleNamespace(
        node_states_json={},
        run_status="executing",
        console_log_json=[],
    )
    run_files = [
        SimpleNamespace(
            task_file_id="file-1",
            file_status="ok",
            result_summary_json={"tsv_rows": [{"memo": "Taxi fare", "amount": "100"}]},
        )
    ]

    _finalize_run_after_vlm(
        run,
        run_files,
        ok_count=1,
        warn_count=0,
        console_message="VLM finished.",
        vlm_node_id="vlm",
    )

    vlm_detail = run.node_states_json["vlm"]["detail"]
    assert vlm_detail["row_count"] == 1
    assert vlm_detail.get("feedback") == "Taxi fare"
    assert vlm_detail.get("ok") == 1
    table_detail = run.node_states_json["table"]["detail"]
    assert table_detail["row_count"] == 1


def test_finalize_run_after_vlm_preserves_rescan_focus_on_nodes():
    run = SimpleNamespace(
        node_states_json={},
        run_status="executing",
        console_log_json=[],
    )
    run_files = [
        SimpleNamespace(
            task_file_id="file-1",
            file_status="ok",
            result_summary_json={"tsv_rows": [{"memo": "Taxi", "amount": "50"}]},
        )
    ]

    _finalize_run_after_vlm(
        run,
        run_files,
        ok_count=1,
        warn_count=0,
        console_message="Re-VLM finished.",
        vlm_node_id="vlm",
        rescan_focus="Wrong amount",
        rescan_note="use JPY total",
    )

    vlm_detail = run.node_states_json["vlm"]["detail"]
    assert vlm_detail["rescan_focus"] == "Wrong amount"
    assert vlm_detail["rescan_note"] == "use JPY total"
    assert vlm_detail["reason"] == "Re-VLM: Wrong amount"

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.graph.nodes.handlers import (
    _feedback_from_rows,
    _node_detail_summary,
    manager_review_node,
    proposal_pool_join_node,
    vlm_judge_node,
    vote_selector_node,
)


def test_feedback_from_rows_uses_memo():
    assert _feedback_from_rows([{"memo": "Japanese taxi receipts detected"}]) == "Japanese taxi receipts detected"


def test_node_detail_summary_includes_feedback_and_reason():
    summary = _node_detail_summary(
        row_count=1,
        feedback="Proposal A memo",
        reason="No majority group",
    )
    assert summary["row_count"] == 1
    assert summary["feedback"] == "Proposal A memo"
    assert summary["reason"] == "No majority group"


def test_node_detail_summary_maps_manager_feedback():
    summary = _node_detail_summary(manager_feedback="Needs manual review", reason="tie break")
    assert summary["feedback"] == "Needs manual review"
    assert summary["reason"] == "tie break"


@pytest.mark.asyncio
async def test_vlm_judge_selects_majority_group():
    run = SimpleNamespace(id="run-1", company_id="co-1", processing_mode="AP", graph_json={}, node_states_json={})
    node = {
        "id": "judge",
        "type": "VLMJudge",
        "data": {"provider": "DeepSeek", "model": "judge-model", "skillKey": "vlm_judge_equivalence"},
    }
    proposal_a = {"proposal_node_id": "a", "merged_ocr": [{"total": "100"}]}
    proposal_b = {"proposal_node_id": "b", "merged_ocr": [{"total": "100"}]}
    proposal_c = {"proposal_node_id": "c", "merged_ocr": [{"total": "200"}]}

    with patch("app.graph.nodes.handlers._incoming_payloads", return_value=[{"proposals": [proposal_a, proposal_b, proposal_c]}]):
        with patch(
            "app.graph.nodes.handlers._call_workflow_vlm",
            new_callable=AsyncMock,
            return_value={
                "provider": "DeepSeek",
                "model": "judge-model",
                "data": {"reason": "majority confirmed"},
                "raw": '{"reason":"majority confirmed"}',
            },
        ) as call_vlm:
            with patch("app.graph.nodes.handlers.record_node_execution") as record:
                await vlm_judge_node(MagicMock(), run, node, None)

    payload = record.call_args.kwargs["payload"]
    call_vlm.assert_awaited_once()
    assert payload["selected_group"]["count"] == 2
    assert "2 of 3" in payload["reason"]
    assert payload["summary"]["reason"] == "majority confirmed"
    assert payload["provider"] == "DeepSeek"
    assert payload["model"] == "judge-model"
    assert payload["vlm_review"]["data"]["reason"] == "majority confirmed"


@pytest.mark.asyncio
async def test_vote_and_manager_pass_selected_rows():
    run = SimpleNamespace(id="run-1", company_id="co-1", processing_mode="AP", graph_json={}, node_states_json={})
    vote_node = {"id": "vote", "type": "VoteSelector", "data": {"policy": "majority"}}
    manager_node = {
        "id": "manager",
        "type": "ManagerReview",
        "data": {"provider": "DeepSeek", "model": "manager-model", "skillKey": "manager_review"},
    }
    selected_group = {"rows": [{"total": "100"}], "count": 2}

    with patch(
        "app.graph.nodes.handlers._incoming_payloads",
        return_value=[{"selected_group": selected_group, "reason": "2 of 3 proposals matched."}],
    ):
        with patch("app.graph.nodes.handlers.record_node_execution") as record:
            await vote_selector_node(MagicMock(), run, vote_node, None)

    vote_payload = record.call_args.kwargs["payload"]
    assert vote_payload["selected_rows"] == [{"total": "100"}]

    with patch("app.graph.nodes.handlers._incoming_payloads", return_value=[vote_payload]):
        with patch(
            "app.graph.nodes.handlers._call_workflow_vlm",
            new_callable=AsyncMock,
            return_value={
                "provider": "DeepSeek",
                "model": "manager-model",
                "data": {"status": "pass", "reason": "manager approved"},
                "raw": '{"status":"pass","reason":"manager approved"}',
            },
        ) as call_vlm:
            with patch("app.graph.nodes.handlers.record_node_execution") as record:
                await manager_review_node(MagicMock(), run, manager_node, None)

    manager_payload = record.call_args.kwargs["payload"]
    call_vlm.assert_awaited_once()
    assert manager_payload["status"] == "pass"
    assert manager_payload["selected_rows"] == [{"total": "100"}]
    assert manager_payload["provider"] == "DeepSeek"
    assert manager_payload["model"] == "manager-model"
    assert manager_payload["reason"] == "manager approved"


@pytest.mark.asyncio
async def test_manager_review_records_node_state_when_vlm_fails():
    run = SimpleNamespace(id="run-1", company_id="co-1", processing_mode="AP", graph_json={}, node_states_json={})
    manager_node = {
        "id": "manager",
        "type": "ManagerReview",
        "data": {"provider": "DeepSeek", "model": "manager-model"},
    }
    vote_payload = {
        "selected_rows": [{"total": "100"}],
        "selected_group": {"rows": [{"total": "100"}], "count": 2},
        "reason": "2 of 3 proposals matched.",
    }

    with patch("app.graph.nodes.handlers._incoming_payloads", return_value=[vote_payload]):
        with patch(
            "app.graph.nodes.handlers._call_workflow_vlm",
            new_callable=AsyncMock,
            side_effect=ValueError("DeepSeek API key missing"),
        ):
            with pytest.raises(ValueError, match="DeepSeek API key missing"):
                await manager_review_node(MagicMock(), run, manager_node, None)

    assert run.node_states_json["manager"]["status"] == "failed"
    assert run.node_states_json["manager"]["detail"] == {
        "error": "DeepSeek API key missing",
        "provider": "DeepSeek",
    }
    assert run.node_states_json["workflow_error"] == "Manager Review failed: DeepSeek API key missing"


@pytest.mark.asyncio
async def test_manager_tie_break_uses_revised_rows():
    run = SimpleNamespace(id="run-1", company_id="co-1", processing_mode="AP", graph_json={}, node_states_json={})
    manager_node = {
        "id": "manager",
        "type": "ManagerReview",
        "data": {"provider": "DeepSeek", "model": "manager-model"},
    }
    vote_payload = {
        "selected_rows": [],
        "reason": "No majority equivalent proposal group was found.",
        "proposals": [
            {"proposal_node_id": "a", "merged_ocr": [{"total": "100"}]},
            {"proposal_node_id": "b", "merged_ocr": [{"total": "101"}]},
        ],
    }

    with patch("app.graph.nodes.handlers._incoming_payloads", return_value=[vote_payload]):
        with patch(
            "app.graph.nodes.handlers._call_workflow_vlm",
            new_callable=AsyncMock,
            return_value={
                "provider": "DeepSeek",
                "model": "manager-model",
                "data": {
                    "status": "pass",
                    "reason": "reconciled tie",
                    "revised_rows": [{"total": "100.50"}],
                },
                "raw": "{}",
            },
        ):
            with patch("app.graph.nodes.handlers.record_node_execution") as record:
                await manager_review_node(MagicMock(), run, manager_node, None)

    payload = record.call_args.kwargs["payload"]
    assert payload["selected_rows"] == [{"total": "100.50"}]
    assert payload["revised_rows"] == [{"total": "100.50"}]
    assert payload["status"] == "pass"


@pytest.mark.asyncio
async def test_vote_selector_forwards_proposals_for_manager_tie_break():
    run = SimpleNamespace(id="run-1", company_id="co-1", processing_mode="AP", graph_json={}, node_states_json={})
    vote_node = {"id": "vote", "type": "VoteSelector", "data": {"policy": "majority"}}
    proposals = [
        {"proposal_node_id": "a", "merged_ocr": [{"total": "100"}]},
        {"proposal_node_id": "b", "merged_ocr": [{"total": "101"}]},
    ]
    judge_report = {
        "proposals": proposals,
        "selected_group": None,
        "reason": "No majority equivalent proposal group was found.",
    }

    with patch("app.graph.nodes.handlers._incoming_payloads", return_value=[judge_report]):
        with patch("app.graph.nodes.handlers.record_node_execution") as record:
            await vote_selector_node(MagicMock(), run, vote_node, None)

    payload = record.call_args.kwargs["payload"]
    assert payload["selected_rows"] == []
    assert payload["proposals"] == proposals


@pytest.mark.asyncio
async def test_proposal_pool_accepts_vlm_stage_payload_without_proposal_node_id():
    run = SimpleNamespace(
        id="run-1",
        company_id="co-1",
        processing_mode="AP",
        graph_json={
            "nodes": [
                {"id": "prop-a", "type": "VLMProposer"},
                {"id": "prop-b", "type": "VLMProposer"},
            ],
            "edges": [],
        },
        node_states_json={},
    )
    pool_node = {"id": "pool", "type": "ProposalPoolJoin"}
    incoming = [
        ("prop-a", {"merged_ocr": [{"total": "100"}]}),
        ("prop-b", {"merged_ocr": [{"total": "101"}]}),
    ]

    with patch("app.graph.nodes.handlers._incoming_payloads_by_source", return_value=incoming):
        with patch("app.graph.nodes.handlers.record_node_execution") as record:
            await proposal_pool_join_node(MagicMock(), run, pool_node, None)

    payload = record.call_args.kwargs["payload"]
    assert payload["summary"]["proposal_count"] == 2
    assert payload["proposals"][0]["proposal_node_id"] == "prop-a"
    assert payload["proposals"][1]["proposal_node_id"] == "prop-b"

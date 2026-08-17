import pytest

from app.graph.default_graphs import build_default_graph
from app.graph.graph_schema_v2 import node_catalog
from app.graph.graph_utils import (
    REQUIRED_RUN_NODE_TYPES,
    find_node_by_type,
    receipt_settings,
    validate_graph_for_execute,
    validate_graph_structure,
)


def test_default_graph_ap_has_receipt_node():
    g = build_default_graph("AP")
    assert find_node_by_type(g, "ReceiptStyle") is not None
    assert find_node_by_type(g, "Files") is not None


def test_default_graph_bank_no_receipt_node():
    g = build_default_graph("BANK")
    assert find_node_by_type(g, "ReceiptStyle") is None


def test_validate_ap_requires_receipt_before_run():
    g = build_default_graph("AP")
    nodes = g["nodes"]
    for n in nodes:
        if n.get("type") == "ReceiptStyle":
            n["data"]["receiptSignal"] = None
            n["data"]["tablePreset"] = None
    with pytest.raises(ValueError, match="Receipt style"):
        validate_graph_for_execute(g, "AP")


def test_validate_structure_allows_blank_draft():
    g = {"nodes": [], "edges": []}
    validate_graph_structure(g)
    with pytest.raises(ValueError, match="Graph missing required nodes"):
        validate_graph_for_execute(g, "AP")


def test_required_run_nodes_exclude_coa_deploy():
    assert "CoADeploy" not in REQUIRED_RUN_NODE_TYPES
    assert "SaveResult" in REQUIRED_RUN_NODE_TYPES
    assert "TableReview" in REQUIRED_RUN_NODE_TYPES
    assert "VLM_API" not in REQUIRED_RUN_NODE_TYPES


def test_default_graph_has_no_coa_deploy_node():
    g = build_default_graph("AP")
    assert find_node_by_type(g, "CoADeploy") is None
    assert find_node_by_type(g, "SaveResult") is not None


def test_receipt_settings_from_graph():
    g = build_default_graph("AR")
    rs, tp = receipt_settings(g)
    assert rs == "guess"
    assert tp == "default"


def test_node_catalog_includes_safe_plugin_nodes():
    types = {node["type"] for node in node_catalog("AP")}
    assert "VLMProposer" in types
    assert "ProposalPoolJoin" in types
    assert "VLMJudge" in types
    assert "VoteSelector" in types


def test_node_catalog_covers_recon_mode():
    types = {node["type"] for node in node_catalog("RECON")}
    assert "VLMProposer" in types
    assert "ExternalApiCall" in types


def test_validate_structure_rejects_unknown_node_type():
    g = build_default_graph("AP")
    g["nodes"].append(
        {
            "id": "unknown",
            "type": "UnknownPlugin",
            "position": {"x": 0, "y": 0},
            "data": {"label": "Unknown"},
        }
    )
    with pytest.raises(ValueError, match="Unknown workflow node type"):
        validate_graph_structure(g)


def test_validate_structure_caps_numeric_node_params():
    g = build_default_graph("AP")
    for node in g["nodes"]:
        if node.get("type") == "VLM_API":
            node["data"]["maxVlmCallsPerDocument"] = 99
    with pytest.raises(ValueError, match="maxVlmCallsPerDocument"):
        validate_graph_structure(g)


def test_validate_structure_allows_draft_without_vlm_api():
    g = build_default_graph("AP")
    g["nodes"] = [node for node in g["nodes"] if node.get("type") != "VLM_API"]
    g["edges"] = [
        edge
        for edge in g["edges"]
        if edge.get("source") != "vlm" and edge.get("target") != "vlm"
    ]
    validate_graph_structure(g)
    with pytest.raises(ValueError, match="connect VLM_API or MergeResult"):
        validate_graph_for_execute(g, "AP")


def test_validate_execute_accepts_proposal_merge_path():
    g = build_default_graph("AP")
    g["nodes"] = [node for node in g["nodes"] if node.get("type") != "VLM_API"]
    g["edges"] = [
        edge
        for edge in g["edges"]
        if edge.get("source") != "vlm" and edge.get("target") != "vlm"
    ]
    g["nodes"].extend(
        [
            {"id": "proposal", "type": "VLMProposer", "position": {"x": 0, "y": 0}, "data": {"nodeType": "VLMProposer"}},
            {"id": "pool", "type": "ProposalPoolJoin", "position": {"x": 0, "y": 0}, "data": {"nodeType": "ProposalPoolJoin"}},
            {"id": "judge", "type": "VLMJudge", "position": {"x": 0, "y": 0}, "data": {"nodeType": "VLMJudge"}},
            {"id": "vote", "type": "VoteSelector", "position": {"x": 0, "y": 0}, "data": {"nodeType": "VoteSelector"}},
            {"id": "merge", "type": "MergeResult", "position": {"x": 0, "y": 0}, "data": {"nodeType": "MergeResult"}},
        ]
    )
    g["edges"].extend(
        [
            {"id": "receipt-proposal", "source": "receipt", "target": "proposal", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "proposal-pool", "source": "proposal", "target": "pool", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "pool-judge", "source": "pool", "target": "judge", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "judge-vote", "source": "judge", "target": "vote", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "vote-merge", "source": "vote", "target": "merge", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "merge-table", "source": "merge", "target": "table", "sourceHandle": "out", "targetHandle": "in"},
        ]
    )
    validate_graph_for_execute(g, "AP")

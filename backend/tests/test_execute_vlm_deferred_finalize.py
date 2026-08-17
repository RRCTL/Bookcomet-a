from app.api.workflows import _safe_template_specs
from app.graph.default_graphs import build_default_graph
from app.graph.graph_utils import is_vote_path, terminal_ocr_producer_node_id


def test_vote_path_detects_proposer_merge_table_chain():
    graph = next(g for name, g, _ in _safe_template_specs("AP") if name == "3 VLM Vote")
    assert is_vote_path(graph) is True
    assert terminal_ocr_producer_node_id(graph, "proposal_a") is False
    assert terminal_ocr_producer_node_id(graph, "merge") is False


def test_linear_vlm_is_terminal_before_table():
    graph = build_default_graph("AP")
    assert is_vote_path(graph) is False
    assert terminal_ocr_producer_node_id(graph, "vlm") is True


def test_ap_double_check_template_keeps_vlm_terminal():
    graph = next(g for name, g, _ in _safe_template_specs("AP") if name == "AP Double Check")
    assert terminal_ocr_producer_node_id(graph, "vlm") is True

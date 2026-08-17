from app.api.workflows import _graph_has_vlm_api, _safe_template_specs
from app.graph.default_graphs import build_default_graph
from app.graph.graph_utils import validate_graph_structure


def test_safe_template_specs_exist_for_all_modes():
    for mode in ("AR", "AP", "BANK", "OTHER", "RECON"):
        specs = _safe_template_specs(mode)
        assert len(specs) >= 3
        assert any(is_default for _name, _graph, is_default in specs)
        for _name, graph, _is_default in specs:
            validate_graph_structure(graph)


def test_three_vlm_vote_uses_proposal_path_only():
    graph = next(graph for name, graph, _is_default in _safe_template_specs("AP") if name == "3 VLM Vote")
    assert not any(node.get("type") == "VLM_API" for node in graph["nodes"])
    assert not any(node.get("type") == "ManagerReview" for node in graph["nodes"])
    assert any(edge.get("target") == "proposal_a" for edge in graph["edges"])
    assert any(edge.get("source") == "vote" and edge.get("target") == "merge" for edge in graph["edges"])
    assert any(edge.get("source") == "merge" and edge.get("target") == "table" for edge in graph["edges"])
    judge = next(node for node in graph["nodes"] if node.get("type") == "VLMJudge")
    assert judge["data"]["provider"]


def test_graph_has_vlm_api_detects_default_graph():
    assert _graph_has_vlm_api(build_default_graph("AP")) is True


def test_graph_has_vlm_api_false_for_canonical_vote_template():
    graph = next(graph for name, graph, _ in _safe_template_specs("AP") if name == "3 VLM Vote")
    assert _graph_has_vlm_api(graph) is False


def test_vote_template_needs_refresh_when_manager_present():
    from app.api.workflows import _vote_template_needs_refresh

    canonical = next(graph for name, graph, _ in _safe_template_specs("AP") if name == "3 VLM Vote")
    assert _vote_template_needs_refresh(canonical) is False
    stale = dict(canonical)
    stale["nodes"] = list(canonical["nodes"]) + [
        {"id": "manager", "type": "ManagerReview", "position": {"x": 0, "y": 0}, "data": {}},
    ]
    assert _vote_template_needs_refresh(stale) is True


def test_manager_review_uses_vlm_manager_merge_path():
    graph = next(graph for name, graph, _ in _safe_template_specs("AP") if name == "Manager Review")
    assert any(node.get("type") == "VLM_API" for node in graph["nodes"])
    assert not any(node.get("type") == "VLMProposer" for node in graph["nodes"])
    assert any(edge.get("source") == "vlm" and edge.get("target") == "manager" for edge in graph["edges"])
    assert any(edge.get("source") == "merge" and edge.get("target") == "table" for edge in graph["edges"])

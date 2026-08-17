from types import SimpleNamespace

import pytest

from app.graph.branch_routing import (
    evaluate_if_condition,
    evaluate_switch_on,
    filter_run_files_for_node,
    node_has_branch_input,
    set_file_routes,
    should_skip_branch_node,
)
from app.graph.default_graphs import build_default_graph


def _rf(task_file_id: str, *, status: str = "ok", gate: str | None = None):
    return SimpleNamespace(task_file_id=task_file_id, file_status=status, gate_result=gate)


def test_if_condition_routes_warning_files():
    files = [_rf("a", status="ok"), _rf("b", status="warning", gate="warn")]
    true_ids, false_ids = evaluate_if_condition("needs_double_check", files)
    assert true_ids == ["b"]
    assert false_ids == ["a"]


def test_switch_routes_by_file_status():
    files = [_rf("a", status="ok"), _rf("b", status="warning"), _rf("c", status="failed")]
    routes = evaluate_switch_on("file_status", files)
    assert routes["out0"] == ["a"]
    assert routes["out1"] == ["b"]
    assert routes["default"] == ["c"]


def test_filter_run_files_for_if_branch():
    graph = build_default_graph("AP")
    graph["nodes"].extend(
        [
            {
                "id": "if1",
                "type": "If",
                "position": {"x": 0, "y": 0},
                "data": {"condition": "needs_double_check"},
            },
            {
                "id": "dc",
                "type": "VLMDoubleCheck",
                "position": {"x": 0, "y": 0},
                "data": {"enabled": True},
            },
        ]
    )
    graph["edges"] = [
        e for e in graph["edges"] if not (e["source"] == "vlm" and e["target"] == "table")
    ]
    graph["edges"].extend(
        [
            {"id": "vlm-if", "source": "vlm", "target": "if1", "sourceHandle": "out", "targetHandle": "in"},
            {
                "id": "if-dc",
                "source": "if1",
                "target": "dc",
                "sourceHandle": "true",
                "targetHandle": "in",
            },
            {"id": "dc-table", "source": "dc", "target": "table", "sourceHandle": "out", "targetHandle": "in"},
        ]
    )
    run = SimpleNamespace(
        graph_json=graph,
        node_states_json={},
    )
    set_file_routes(run, "if1", {"true": ["file-b"], "false": ["file-a"]})
    files = [_rf("file-a"), _rf("file-b")]
    dc_node = next(n for n in graph["nodes"] if n["id"] == "dc")
    filtered = filter_run_files_for_node(run, dc_node, files)
    assert [f.task_file_id for f in filtered] == ["file-b"]
    assert should_skip_branch_node(run, dc_node, files) is False


def test_should_skip_when_branch_empty():
    graph = build_default_graph("BANK")
    graph["nodes"].append(
        {"id": "if1", "type": "If", "position": {"x": 0, "y": 0}, "data": {}}
    )
    graph["edges"] = [
        e for e in graph["edges"] if not (e["source"] == "vlm" and e["target"] == "table")
    ]
    graph["edges"].append(
        {
            "id": "if-table",
            "source": "if1",
            "target": "table",
            "sourceHandle": "false",
            "targetHandle": "in",
        }
    )
    run = SimpleNamespace(graph_json=graph, node_states_json={})
    set_file_routes(run, "if1", {"true": ["x"], "false": []})
    table_node = next(n for n in graph["nodes"] if n["id"] == "table")
    assert should_skip_branch_node(run, table_node, [_rf("a")]) is True


def test_node_has_branch_input_detects_if_source():
    graph = build_default_graph("AP")
    graph["nodes"].append({"id": "if1", "type": "If", "position": {"x": 0, "y": 0}, "data": {}})
    graph["edges"].append(
        {"id": "if-vlm", "source": "if1", "target": "vlm", "sourceHandle": "false", "targetHandle": "in"}
    )
    assert node_has_branch_input(graph, "vlm") is True
    assert node_has_branch_input(graph, "receipt") is False


def test_filter_empty_branch_route_returns_no_files():
    graph = build_default_graph("AP")
    graph["nodes"].append({"id": "if1", "type": "If", "position": {"x": 0, "y": 0}, "data": {}})
    graph["edges"] = [e for e in graph["edges"] if not (e["source"] == "receipt" and e["target"] == "vlm")]
    graph["edges"].extend(
        [
            {"id": "r-if", "source": "receipt", "target": "if1", "sourceHandle": "out", "targetHandle": "in"},
            {"id": "if-vlm", "source": "if1", "target": "vlm", "sourceHandle": "true", "targetHandle": "in"},
        ]
    )
    run = SimpleNamespace(graph_json=graph, node_states_json={})
    set_file_routes(run, "if1", {"true": [], "false": ["f1"]})
    vlm_node = next(n for n in graph["nodes"] if n["id"] == "vlm")
    files = [_rf("f1", status="pending")]
    assert filter_run_files_for_node(run, vlm_node, files) == []

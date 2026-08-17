"""File-level branch routing for If/Switch workflow nodes."""

from __future__ import annotations

from typing import Any

from app.graph.graph_utils import find_node_by_id
from app.models.workflow import WorkflowRun, WorkflowRunFile


def _file_routes(run: WorkflowRun) -> dict[str, dict[str, list[str]]]:
    states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
    routes = states.get("file_routes")
    return routes if isinstance(routes, dict) else {}


def set_file_routes(
    run: WorkflowRun,
    node_id: str,
    routes: dict[str, list[str]],
) -> None:
    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    all_routes = dict(_file_routes(run))
    all_routes[node_id] = routes
    states["file_routes"] = all_routes
    run.node_states_json = states


def incoming_edge(graph_json: dict[str, Any] | None, node_id: str) -> dict[str, Any] | None:
    edges = incoming_edges(graph_json, node_id)
    return edges[0] if edges else None


def incoming_edges(graph_json: dict[str, Any] | None, node_id: str) -> list[dict[str, Any]]:
    if not isinstance(graph_json, dict):
        return []
    edges = graph_json.get("edges")
    if not isinstance(edges, list):
        return []
    return [edge for edge in edges if isinstance(edge, dict) and edge.get("target") == node_id]


def evaluate_if_condition(
    condition: str,
    run_files: list[WorkflowRunFile],
) -> tuple[list[str], list[str]]:
    true_ids: list[str] = []
    false_ids: list[str] = []
    for rf in run_files:
        fid = rf.task_file_id
        if condition == "needs_double_check":
            if rf.file_status == "warning" or rf.gate_result:
                true_ids.append(fid)
            else:
                false_ids.append(fid)
        elif condition == "vlm_warning":
            if rf.file_status == "warning":
                true_ids.append(fid)
            else:
                false_ids.append(fid)
        elif condition == "file_failed":
            if rf.file_status in ("failed", "error"):
                true_ids.append(fid)
            else:
                false_ids.append(fid)
        else:
            true_ids.append(fid)
    return true_ids, false_ids


def evaluate_switch_on(
    switch_on: str,
    run_files: list[WorkflowRunFile],
) -> dict[str, list[str]]:
    routes: dict[str, list[str]] = {"out0": [], "out1": [], "default": []}
    for rf in run_files:
        fid = rf.task_file_id
        if switch_on == "file_status":
            if rf.file_status == "ok":
                routes["out0"].append(fid)
            elif rf.file_status == "warning":
                routes["out1"].append(fid)
            else:
                routes["default"].append(fid)
        elif switch_on == "processing_mode":
            routes["default"].append(fid)
        else:
            routes["default"].append(fid)
    return routes


def _branch_incoming_edge(graph_json: dict[str, Any] | None, node_id: str) -> dict[str, Any] | None:
    for edge in incoming_edges(graph_json, node_id):
        source_node = find_node_by_id(graph_json, str(edge.get("source") or ""))
        if source_node and str(source_node.get("type") or "") in ("If", "Switch"):
            return edge
    return None


def node_has_branch_input(graph_json: dict[str, Any] | None, node_id: str) -> bool:
    return _branch_incoming_edge(graph_json, node_id) is not None


def filter_run_files_for_node(
    run: WorkflowRun,
    node: dict[str, Any],
    run_files: list[WorkflowRunFile],
) -> list[WorkflowRunFile]:
    node_id = str(node.get("id") or "")
    edge = _branch_incoming_edge(run.graph_json, node_id)
    if not edge:
        return run_files

    source_id = str(edge.get("source") or "")
    source_handle = str(edge.get("sourceHandle") or "out")
    routes = _file_routes(run).get(source_id, {})
    allowed = set(routes.get(source_handle, []))
    if not allowed:
        return []
    return [rf for rf in run_files if rf.task_file_id in allowed]


def should_skip_branch_node(
    run: WorkflowRun,
    node: dict[str, Any],
    run_files: list[WorkflowRunFile],
) -> bool:
    node_id = str(node.get("id") or "")
    edges = incoming_edges(run.graph_json, node_id)
    branch_edges: list[dict[str, Any]] = []
    for edge in edges:
        source_id = str(edge.get("source") or "")
        source_node = find_node_by_id(run.graph_json, source_id)
        if not source_node:
            continue
        if str(source_node.get("type") or "") in ("If", "Switch"):
            branch_edges.append(edge)
    if not branch_edges:
        return False
    for edge in branch_edges:
        source_id = str(edge.get("source") or "")
        source_handle = str(edge.get("sourceHandle") or "out")
        routes = _file_routes(run).get(source_id, {})
        if routes.get(source_handle):
            return False
    return True

from __future__ import annotations

from collections import deque
from typing import Any, Optional

from app.graph.graph_schema_v2 import (
    GRAPH_VERSION,
    NODE_SPECS,
    edges_compatible,
    ensure_graph_v2,
    graph_version,
)

REQUIRED_RUN_NODE_TYPES = frozenset(
    {"Files", "ModeConfig", "TableReview", "SaveResult"}
)

OCR_PRODUCER_NODE_TYPES = frozenset({"VLM_API", "VLMProposer", "VLMDoubleCheck"})


def graph_nodes(graph_json: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(graph_json, dict):
        return []
    nodes = graph_json.get("nodes")
    return nodes if isinstance(nodes, list) else []


def find_node_by_type(graph_json: dict[str, Any] | None, node_type: str) -> Optional[dict[str, Any]]:
    for node in graph_nodes(graph_json):
        if not isinstance(node, dict):
            continue
        if node.get("type") == node_type:
            return node
        data = node.get("data")
        if isinstance(data, dict) and data.get("nodeType") == node_type:
            return node
    return None


def find_node_by_id(graph_json: dict[str, Any] | None, node_id: str) -> Optional[dict[str, Any]]:
    for node in graph_nodes(graph_json):
        if isinstance(node, dict) and node.get("id") == node_id:
            return node
    return None


def node_data(node: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(node, dict):
        return {}
    data = node.get("data")
    return data if isinstance(data, dict) else {}


def receipt_settings(graph_json: dict[str, Any] | None) -> tuple[str | None, str | None]:
    node = find_node_by_type(graph_json, "ReceiptStyle")
    data = node_data(node)
    receipt = data.get("receiptSignal")
    table = data.get("tablePreset")
    rs = str(receipt).strip() if receipt is not None else None
    tp = str(table).strip() if table is not None else None
    return rs or None, tp or None


def receipt_settings_for_run(
    graph_json: dict[str, Any] | None,
    node_states_json: dict[str, Any] | None = None,
) -> tuple[str | None, str | None]:
    """Resolve receipt/table preset from the current graph (per-batch snapshot on files)."""
    del node_states_json
    return receipt_settings(graph_json)


def graph_has_merge_result(graph_json: dict[str, Any] | None) -> bool:
    return find_node_by_type(graph_json, "MergeResult") is not None


def is_vote_path(graph_json: dict[str, Any] | None) -> bool:
    """True when MergeResult feeds Table and at least one VLMProposer exists."""
    if not graph_has_merge_result(graph_json):
        return False
    if not find_node_by_type(graph_json, "VLMProposer"):
        return False
    merge_ids = _node_ids_by_type(graph_json, {"MergeResult"})
    table_ids = _node_ids_by_type(graph_json, {"TableReview"})
    return _has_path(graph_json, merge_ids, table_ids)


def terminal_ocr_producer_node_id(
    graph_json: dict[str, Any] | None,
    node_id: str,
) -> bool:
    """True when this OCR producer is the last one on any path to TableReview."""
    if not isinstance(graph_json, dict):
        return False
    if graph_has_merge_result(graph_json):
        return False
    node = find_node_by_id(graph_json, node_id)
    if not node or str(node.get("type") or "") not in OCR_PRODUCER_NODE_TYPES:
        return False
    table_ids = _node_ids_by_type(graph_json, {"TableReview"})
    if not table_ids:
        return False
    edges = graph_json.get("edges") if isinstance(graph_json.get("edges"), list) else []
    producer_ids = _node_ids_by_type(graph_json, OCR_PRODUCER_NODE_TYPES)
    if node_id not in producer_ids:
        return False
    for other_id in producer_ids:
        if other_id == node_id:
            continue
        if _has_path(graph_json, {node_id}, {other_id}) and _has_path(graph_json, {other_id}, table_ids):
            return False
    return _has_path(graph_json, {node_id}, table_ids)


def vlm_settings(graph_json: dict[str, Any] | None) -> tuple[str, bool]:
    node = find_node_by_type(graph_json, "VLM_API")
    data = node_data(node)
    provider = str(data.get("provider") or "Qwen")
    cross = bool(data.get("crossVlm"))
    has_dc = find_node_by_type(graph_json, "VLMDoubleCheck") is not None
    return provider, cross or has_dc


def vlm_node_params(graph_json: dict[str, Any] | None) -> dict[str, Any]:
    node = find_node_by_type(graph_json, "VLM_API")
    data = node_data(node)
    return {
        "provider": str(data.get("provider") or "Qwen"),
        "model": data.get("model"),
        "crossVlm": bool(data.get("crossVlm")),
        "promptPreset": str(data.get("promptPreset") or "default"),
    }


def double_check_node_params(graph_json: dict[str, Any] | None) -> dict[str, Any] | None:
    from app.core.config import default_workflow_provider

    node = find_node_by_type(graph_json, "VLMDoubleCheck")
    if not node:
        return None
    data = node_data(node)
    return {
        "provider": str(data.get("provider") or default_workflow_provider()),
        "model": data.get("model"),
        "mergePolicy": str(data.get("mergePolicy") or "cross_vlm"),
        "enabled": data.get("enabled", True) is not False,
    }


def ap_vlm_model_override_for_provider(provider: str) -> str | None:
    return None


def model_override_for_mode(mode: str, provider: str, explicit_model: str | None) -> str | None:
    if explicit_model and str(explicit_model).strip():
        return str(explicit_model).strip()
    return ap_vlm_model_override_for_provider(provider)


def validate_graph_edges(graph_json: dict[str, Any] | None) -> None:
    if not isinstance(graph_json, dict):
        raise ValueError("Invalid graph")
    edges = graph_json.get("edges")
    if not isinstance(edges, list):
        return
    for e in edges:
        if not isinstance(e, dict):
            continue
        src_id, tgt_id = e.get("source"), e.get("target")
        src = find_node_by_id(graph_json, str(src_id or ""))
        tgt = find_node_by_id(graph_json, str(tgt_id or ""))
        if not src or not tgt:
            continue
        src_type = str(src.get("type") or "")
        tgt_type = str(tgt.get("type") or "")
        if not edges_compatible(
            src_type,
            tgt_type,
            e.get("sourceHandle"),
            e.get("targetHandle"),
        ):
            raise ValueError(
                f"Incompatible edge {src_id} -> {tgt_id}: "
                f"{src_type}.{e.get('sourceHandle', 'out')} to {tgt_type}.{e.get('targetHandle', 'in')}"
            )


def validate_graph_node_types(graph_json: dict[str, Any] | None) -> None:
    for node in graph_nodes(graph_json):
        if not isinstance(node, dict):
            raise ValueError("Invalid graph node")
        node_type = str(node.get("type") or "")
        if node_type not in NODE_SPECS:
            raise ValueError(f"Unknown workflow node type: {node_type or 'missing'}")


def validate_graph_node_params(graph_json: dict[str, Any] | None) -> None:
    for node in graph_nodes(graph_json):
        node_type = str(node.get("type") or "")
        spec = NODE_SPECS.get(node_type) or {}
        params = spec.get("params") if isinstance(spec.get("params"), dict) else {}
        data = node_data(node)
        for key, param_spec in params.items():
            if not isinstance(param_spec, dict) or key not in data:
                continue
            raw = data.get(key)
            if raw is None:
                continue
            ptype = str(param_spec.get("type") or "")
            if ptype != "number":
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Invalid numeric value for {node_type}.{key}") from exc
            min_value = param_spec.get("min")
            max_value = param_spec.get("max")
            if min_value is not None and value < float(min_value):
                raise ValueError(f"{node_type}.{key} must be >= {min_value}")
            if max_value is not None and value > float(max_value):
                raise ValueError(f"{node_type}.{key} must be <= {max_value}")


def validate_no_cycles(graph_json: dict[str, Any] | None) -> None:
    nodes = graph_nodes(graph_json)
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}
    edges = graph_json.get("edges") if isinstance(graph_json, dict) else []
    indeg: dict[str, int] = {nid: 0 for nid in by_id}
    adj: dict[str, list[str]] = {nid: [] for nid in by_id}
    for e in edges or []:
        if not isinstance(e, dict):
            continue
        src, tgt = e.get("source"), e.get("target")
        if src in by_id and tgt in by_id:
            adj[str(src)].append(str(tgt))
            indeg[str(tgt)] = indeg.get(str(tgt), 0) + 1
    q = deque([nid for nid, d in indeg.items() if d == 0])
    count = 0
    while q:
        nid = q.popleft()
        count += 1
        for nxt in adj.get(nid, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                q.append(nxt)
    if count != len(by_id):
        raise ValueError("Graph contains a cycle")


def _node_ids_by_type(graph_json: dict[str, Any] | None, node_types: set[str]) -> set[str]:
    return {
        str(node.get("id"))
        for node in graph_nodes(graph_json)
        if isinstance(node, dict) and str(node.get("type") or "") in node_types and node.get("id")
    }


def _has_path(graph_json: dict[str, Any] | None, sources: set[str], targets: set[str]) -> bool:
    if not sources or not targets:
        return False
    edges = graph_json.get("edges") if isinstance(graph_json, dict) else []
    adj: dict[str, list[str]] = {}
    for edge in edges or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source and target:
            adj.setdefault(source, []).append(target)
    q = deque(sources)
    seen = set(sources)
    while q:
        node_id = q.popleft()
        if node_id in targets:
            return True
        for nxt in adj.get(node_id, []):
            if nxt not in seen:
                seen.add(nxt)
                q.append(nxt)
    return False


def validate_runnable_ocr_path(graph_json: dict[str, Any] | None) -> None:
    producers = _node_ids_by_type(graph_json, {"VLM_API", "MergeResult", "VLMDoubleCheck"})
    table_nodes = _node_ids_by_type(graph_json, {"TableReview"})
    if not _has_path(graph_json, producers, table_nodes):
        raise ValueError("Workflow must connect VLM_API or MergeResult to TableReview before Run")


def validate_graph_for_execute(graph_json: dict[str, Any] | None, processing_mode: str) -> None:
    g = ensure_graph_v2(graph_json, processing_mode)
    validate_graph_node_types(g)
    validate_graph_node_params(g)
    missing = [t for t in REQUIRED_RUN_NODE_TYPES if not find_node_by_type(g, t)]
    if missing:
        raise ValueError(f"Graph missing required nodes: {', '.join(missing)}")
    mode = (processing_mode or "").upper()
    if mode in ("AR", "AP") and find_node_by_type(g, "ReceiptStyle"):
        receipt, table = receipt_settings(g)
        if not receipt or not table:
            raise ValueError("Receipt style and table preset are required for AR/AP before Run")
    validate_graph_edges(g)
    validate_no_cycles(g)
    validate_runnable_ocr_path(g)


def validate_graph_structure(graph_json: dict[str, Any] | None) -> None:
    g = graph_json
    if isinstance(g, dict) and graph_version(g) < GRAPH_VERSION:
        mode = str(g.get("processingMode") or "AR")
        g = ensure_graph_v2(g, mode)
    validate_graph_node_types(g)
    validate_graph_node_params(g)
    validate_graph_edges(g)
    validate_no_cycles(g)

"""Workflow graph path helpers and execution fingerprints."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.models.workflow import WorkflowRun


def _node_fingerprint_payload(node: dict[str, Any], *, exclude_receipt_style: bool) -> dict[str, Any]:
    data = node.get("data")
    if exclude_receipt_style and node.get("type") == "ReceiptStyle" and isinstance(data, dict):
        data = {k: v for k, v in data.items() if k not in ("receiptSignal", "tablePreset")}
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "data": data,
    }


def graph_topology_fingerprint(graph_json: dict[str, Any] | None) -> str:
    """Hash node ids/types and edges only (batch template swaps, not receipt/table presets)."""
    if not isinstance(graph_json, dict):
        return ""
    nodes = [
        (n.get("id"), n.get("type"))
        for n in (graph_json.get("nodes") or [])
        if isinstance(n, dict)
    ]
    edges = [
        (
            e.get("source"),
            e.get("target"),
            e.get("sourceHandle"),
            e.get("targetHandle"),
        )
        for e in (graph_json.get("edges") or [])
        if isinstance(e, dict)
    ]
    payload = {
        "nodes": sorted(nodes),
        "edges": sorted(edges),
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def clear_executed_graph_hash(run: WorkflowRun) -> None:
    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    states.pop("executed_graph_hash", None)
    states.pop("executed_graph_hash_v2", None)
    run.node_states_json = states


def clear_executed_graph_hash_if_topology_changed(
    run: WorkflowRun,
    new_graph_json: dict[str, Any],
) -> bool:
    """Drop execute fingerprint when workflow nodes/edges topology changes (e.g. template deploy)."""
    states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
    if not states.get("executed_graph_hash"):
        return False
    old_topo = graph_topology_fingerprint(run.graph_json)
    new_topo = graph_topology_fingerprint(new_graph_json)
    if not old_topo or not new_topo or old_topo == new_topo:
        return False
    clear_executed_graph_hash(run)
    return True


def graph_execution_fingerprint(
    graph_json: dict[str, Any] | None,
    *,
    exclude_receipt_style: bool = True,
) -> str:
    if not isinstance(graph_json, dict):
        return ""
    payload = {
        "nodes": [
            _node_fingerprint_payload(n, exclude_receipt_style=exclude_receipt_style)
            for n in (graph_json.get("nodes") or [])
            if isinstance(n, dict)
        ],
        "edges": graph_json.get("edges") or [],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def freeze_receipt_settings_on_run(run: WorkflowRun) -> None:
    from app.graph.graph_utils import receipt_settings

    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    if isinstance(states.get("frozen_receipt_settings"), dict):
        return
    receipt, table = receipt_settings(run.graph_json)
    states["frozen_receipt_settings"] = {
        "receiptSignal": receipt,
        "tablePreset": table,
    }
    run.node_states_json = states


def _migrate_executed_graph_hash_v2(run: WorkflowRun, states: dict[str, Any], current: str) -> None:
    states["executed_graph_hash"] = current
    states["executed_graph_hash_v2"] = True
    run.node_states_json = states


def assert_graph_unchanged_or_raise(run: WorkflowRun) -> None:
    from fastapi import HTTPException

    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    prior = states.get("executed_graph_hash")
    if not prior:
        return
    current = graph_execution_fingerprint(run.graph_json)
    if current and prior == current:
        return
    if not states.get("executed_graph_hash_v2"):
        # Legacy v1 hash included ReceiptStyle fields; upgrade to v2 (receipt-excluded).
        legacy_full = graph_execution_fingerprint(run.graph_json, exclude_receipt_style=False)
        if prior == legacy_full:
            _migrate_executed_graph_hash_v2(run, states, current)
            return
        # Receipt layout may have changed since batch 1; allow and re-pin v2 fingerprint.
        if current:
            _migrate_executed_graph_hash_v2(run, states, current)
            return
    raise HTTPException(
        status_code=400,
        detail="Graph changed after a previous Run. Use Re-VLM or create a new run.",
    )


def store_executed_graph_hash(run: WorkflowRun) -> None:
    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    states["executed_graph_hash"] = graph_execution_fingerprint(run.graph_json)
    states["executed_graph_hash_v2"] = True
    run.node_states_json = states

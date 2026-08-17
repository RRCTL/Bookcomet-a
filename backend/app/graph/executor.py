"""Topological workflow executor (ComfyUI-style node pipeline)."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from app.graph.graph_utils import graph_nodes, validate_graph_for_execute, validate_no_cycles
from app.graph.vlm_call_budget import NON_VITAL_NODE_TYPES
from app.graph.workflow_service import _set_node_state
from app.models.workflow import WorkflowRun

EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]
NodeHandler = Callable[[Session, WorkflowRun, dict[str, Any], EmitFn | None], Awaitable[None]]


def _node_data(node: dict[str, Any]) -> dict[str, Any]:
    data = node.get("data")
    return data if isinstance(data, dict) else {}


async def _run_handler_with_guards(
    handler: NodeHandler,
    db: Session,
    run: WorkflowRun,
    node: dict[str, Any],
    emit: EmitFn | None,
) -> None:
    data = _node_data(node)
    try:
        max_retries = int(data.get("maxRetries") or 0)
    except (TypeError, ValueError):
        max_retries = 0
    max_retries = max(0, min(max_retries, 5))
    try:
        timeout_ms = int(data.get("timeoutMs") or 0)
    except (TypeError, ValueError):
        timeout_ms = 0

    attempts = max_retries + 1
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            coro = handler(db, run, node, emit)
            if timeout_ms > 0:
                await asyncio.wait_for(coro, timeout=timeout_ms / 1000)
            else:
                await coro
            return
        except Exception as exc:
            last_error = exc
            if emit:
                await emit(
                    "node_retry",
                    {
                        "run_id": run.id,
                        "node_id": str(node.get("id") or node.get("type") or ""),
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "error": str(exc)[:500],
                    },
                )
            if attempt >= attempts:
                raise
    if last_error:
        raise last_error


def topo_node_order(graph_json: dict[str, Any]) -> list[dict[str, Any]]:
    nodes = graph_nodes(graph_json)
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}
    edges = graph_json.get("edges") if isinstance(graph_json.get("edges"), list) else []
    indeg: dict[str, int] = {nid: 0 for nid in by_id}
    adj: dict[str, list[str]] = {nid: [] for nid in by_id}
    for e in edges:
        if not isinstance(e, dict):
            continue
        src, tgt = e.get("source"), e.get("target")
        if src in by_id and tgt in by_id:
            adj[str(src)].append(str(tgt))
            indeg[str(tgt)] = indeg.get(str(tgt), 0) + 1
    q = deque([nid for nid, d in indeg.items() if d == 0])
    ordered: list[str] = []
    while q:
        nid = q.popleft()
        ordered.append(nid)
        for nxt in adj.get(nid, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                q.append(nxt)
    if len(ordered) != len(by_id):
        raise ValueError("Graph contains a cycle")
    return [by_id[nid] for nid in ordered if nid in by_id]


async def run_workflow_graph(
    db: Session,
    run: WorkflowRun,
    handlers: dict[str, NodeHandler],
    *,
    emit: EmitFn | None = None,
    start_at: str | None = None,
    stop_after: str | None = None,
) -> None:
    validate_graph_for_execute(run.graph_json, run.processing_mode)
    if emit:
        await emit("execution_start", {"run_id": run.id, "run_status": run.run_status})

    active = start_at is None
    start_found = start_at is None
    for node in topo_node_order(run.graph_json):
        node_id = str(node.get("id") or node.get("type") or "")
        if not active:
            if node_id == start_at:
                active = True
                start_found = True
            else:
                continue

        ntype = str(node.get("type") or "")
        handler = handlers.get(ntype)
        if handler:
            if emit:
                await emit("node_start", {"run_id": run.id, "node_id": node_id})
            try:
                await _run_handler_with_guards(handler, db, run, node, emit)
            except Exception as exc:
                if ntype in NON_VITAL_NODE_TYPES:
                    _set_node_state(run, node_id, "failed", {"error": str(exc)[:200]})
                    db.commit()
                    if emit:
                        await emit(
                            "node_failed",
                            {"run_id": run.id, "node_id": node_id, "error": str(exc)[:500]},
                        )
                else:
                    raise
            if emit:
                await emit("node_complete", {"run_id": run.id, "node_id": node_id})

        if stop_after and node_id == stop_after:
            db.commit()
            if emit:
                await emit(
                    "execution_complete",
                    {
                        "run_id": run.id,
                        "run_status": run.run_status,
                        "node_states_json": run.node_states_json,
                    },
                )
            return

    if start_at and not start_found:
        raise ValueError(f"start_at node not found: {start_at}")

    if emit:
        await emit(
            "execution_complete",
            {
                "run_id": run.id,
                "run_status": run.run_status,
                "node_states_json": run.node_states_json,
            },
        )

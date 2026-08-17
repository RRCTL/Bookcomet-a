"""Per-run VLM/OCR call budget tracking (N cap per document)."""

from __future__ import annotations

from typing import Any

from app.graph.graph_utils import node_data
from app.models.workflow import WorkflowRun

DEFAULT_VLM_CALLS_PER_DOCUMENT = 3

OCR_PRODUCER_NODE_TYPES = frozenset({"VLM_API", "VLMProposer", "VLMDoubleCheck"})

NON_VITAL_NODE_TYPES = frozenset({"VLMProposer", "VLMJudge"})


def _states(run: WorkflowRun) -> dict[str, Any]:
    raw = run.node_states_json
    return dict(raw) if isinstance(raw, dict) else {}


def vlm_calls_by_file(run: WorkflowRun) -> dict[str, int]:
    raw = _states(run).get("vlm_calls_by_file")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for key, value in raw.items():
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out


def get_vlm_call_count(run: WorkflowRun, task_file_id: str) -> int:
    return vlm_calls_by_file(run).get(task_file_id, 0)


def max_vlm_calls_for_node(node: dict[str, Any] | None) -> int:
    if not node:
        return DEFAULT_VLM_CALLS_PER_DOCUMENT
    data = node_data(node)
    try:
        cap = int(data.get("maxVlmCallsPerDocument") or DEFAULT_VLM_CALLS_PER_DOCUMENT)
    except (TypeError, ValueError):
        cap = DEFAULT_VLM_CALLS_PER_DOCUMENT
    return max(1, min(cap, 8))


def can_make_vlm_call(run: WorkflowRun, node: dict[str, Any] | None, task_file_id: str) -> bool:
    return get_vlm_call_count(run, task_file_id) < max_vlm_calls_for_node(node)


def record_vlm_call(run: WorkflowRun, task_file_id: str) -> int:
    states = _states(run)
    counts = vlm_calls_by_file(run)
    next_count = counts.get(task_file_id, 0) + 1
    counts[task_file_id] = next_count
    states["vlm_calls_by_file"] = counts
    run.node_states_json = states
    return next_count

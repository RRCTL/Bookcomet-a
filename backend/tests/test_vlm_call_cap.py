from types import SimpleNamespace

from app.graph.vlm_call_budget import (
    DEFAULT_VLM_CALLS_PER_DOCUMENT,
    can_make_vlm_call,
    max_vlm_calls_for_node,
    record_vlm_call,
)


def _run_with_counts(counts: dict[str, int] | None = None):
    return SimpleNamespace(node_states_json={"vlm_calls_by_file": counts or {}})


def test_max_vlm_calls_defaults_to_three():
    assert max_vlm_calls_for_node(None) == DEFAULT_VLM_CALLS_PER_DOCUMENT
    assert max_vlm_calls_for_node({"data": {}}) == 3
    assert max_vlm_calls_for_node({"data": {"maxVlmCallsPerDocument": 8}}) == 8
    assert max_vlm_calls_for_node({"data": {"maxVlmCallsPerDocument": 99}}) == 8


def test_record_vlm_call_increments_per_file():
    run = _run_with_counts()
    assert record_vlm_call(run, "file-a") == 1
    assert record_vlm_call(run, "file-a") == 2
    assert record_vlm_call(run, "file-b") == 1
    assert run.node_states_json["vlm_calls_by_file"] == {"file-a": 2, "file-b": 1}


def test_can_make_vlm_call_blocks_fourth_call():
    run = _run_with_counts({"file-a": 3})
    node = {"data": {"maxVlmCallsPerDocument": 3}}
    assert can_make_vlm_call(run, node, "file-a") is False
    assert can_make_vlm_call(run, node, "file-b") is True

import copy

import pytest
from fastapi import HTTPException

from app.graph.default_graphs import build_default_graph
from app.graph.graph_utils import (
    receipt_settings,
    receipt_settings_for_run,
    validate_graph_for_execute,
)
from app.api.workflows import _with_double_check, _with_manager_review
from app.graph.workflow_path import (
    assert_graph_unchanged_or_raise,
    clear_executed_graph_hash_if_topology_changed,
    graph_execution_fingerprint,
    graph_topology_fingerprint,
)
from app.models.workflow import WorkflowRun


def _receipt_node(graph):
    for node in graph["nodes"]:
        if node.get("type") == "ReceiptStyle":
            return node
    raise AssertionError("ReceiptStyle node missing")


def test_fingerprint_unchanged_when_only_receipt_signal_changes():
    base = build_default_graph("AP")
    changed = copy.deepcopy(base)
    _receipt_node(changed)["data"]["receiptSignal"] = "multi_page"
    _receipt_node(changed)["data"]["tablePreset"] = "per_page"

    assert graph_execution_fingerprint(base) == graph_execution_fingerprint(changed)


def test_fingerprint_changes_when_vlm_provider_changes():
    base = build_default_graph("AP")
    changed = copy.deepcopy(base)
    for node in changed["nodes"]:
        if node.get("type") == "VLM_API":
            node["data"]["provider"] = "DeepSeek"
            break

    assert graph_execution_fingerprint(base) != graph_execution_fingerprint(changed)


def test_fingerprint_changes_when_receipt_style_node_removed():
    base = build_default_graph("AP")
    changed = copy.deepcopy(base)
    changed["nodes"] = [n for n in changed["nodes"] if n.get("type") != "ReceiptStyle"]

    assert graph_execution_fingerprint(base) != graph_execution_fingerprint(changed)


def test_fingerprint_includes_receipt_style_when_not_excluded():
    base = build_default_graph("AP")
    changed = copy.deepcopy(base)
    _receipt_node(changed)["data"]["receiptSignal"] = "multi_page"

    assert graph_execution_fingerprint(base, exclude_receipt_style=False) != graph_execution_fingerprint(
        changed, exclude_receipt_style=False
    )


def test_validate_graph_for_execute_still_requires_receipt_and_table():
    g = build_default_graph("AP")
    _receipt_node(g)["data"]["receiptSignal"] = None
    _receipt_node(g)["data"]["tablePreset"] = None
    with pytest.raises(ValueError, match="Receipt style"):
        validate_graph_for_execute(g, "AP")


def test_receipt_settings_for_run_uses_current_graph_not_frozen():
    g = build_default_graph("AP")
    _receipt_node(g)["data"]["receiptSignal"] = "multi_page"
    _receipt_node(g)["data"]["tablePreset"] = "per_page"
    frozen_states = {
        "frozen_receipt_settings": {
            "receiptSignal": "guess",
            "tablePreset": "default",
        }
    }

    rs, tp = receipt_settings_for_run(g, frozen_states)
    assert rs == "multi_page"
    assert tp == "per_page"
    assert receipt_settings(g) == (rs, tp)


def test_assert_graph_unchanged_allows_receipt_layout_change_after_run():
    base = build_default_graph("AP")
    prior_hash = graph_execution_fingerprint(base)
    run = WorkflowRun(
        id="run-1",
        graph_json=copy.deepcopy(base),
        node_states_json={"executed_graph_hash": prior_hash, "executed_graph_hash_v2": True},
    )
    _receipt_node(run.graph_json)["data"]["receiptSignal"] = "multi_per_page"

    assert_graph_unchanged_or_raise(run)


def test_assert_graph_unchanged_rejects_vlm_provider_change_after_run():
    base = build_default_graph("AP")
    prior_hash = graph_execution_fingerprint(base)
    run = WorkflowRun(
        id="run-1",
        graph_json=copy.deepcopy(base),
        node_states_json={"executed_graph_hash": prior_hash, "executed_graph_hash_v2": True},
    )
    for node in run.graph_json["nodes"]:
        if node.get("type") == "VLM_API":
            node["data"]["provider"] = "DeepSeek"
            break

    with pytest.raises(HTTPException) as exc:
        assert_graph_unchanged_or_raise(run)
    assert exc.value.status_code == 400


def test_assert_graph_unchanged_migrates_legacy_v1_hash_on_receipt_change():
    base = build_default_graph("AP")
    prior_v1 = graph_execution_fingerprint(base, exclude_receipt_style=False)
    run = WorkflowRun(
        id="run-1",
        graph_json=copy.deepcopy(base),
        node_states_json={"executed_graph_hash": prior_v1},
    )
    _receipt_node(run.graph_json)["data"]["receiptSignal"] = "multi_per_page"

    assert_graph_unchanged_or_raise(run)

    states = run.node_states_json
    assert states.get("executed_graph_hash_v2") is True
    assert states.get("executed_graph_hash") == graph_execution_fingerprint(run.graph_json)


def test_topology_fingerprint_differs_for_double_check_vs_manager_review():
    double_check = _with_double_check("AP")
    manager_review = _with_manager_review("AP")
    assert graph_topology_fingerprint(double_check) != graph_topology_fingerprint(manager_review)


def test_clear_hash_when_topology_changes():
    run = WorkflowRun(
        id="run-1",
        graph_json=_with_double_check("AP"),
        node_states_json={"executed_graph_hash": "pinned", "executed_graph_hash_v2": True},
    )
    manager_graph = _with_manager_review("AP")

    assert clear_executed_graph_hash_if_topology_changed(run, manager_graph) is True
    assert run.node_states_json.get("executed_graph_hash") is None
    assert run.node_states_json.get("executed_graph_hash_v2") is None


def test_keeps_hash_when_only_receipt_or_table_preset_changes():
    base = build_default_graph("AP")
    run = WorkflowRun(
        id="run-1",
        graph_json=copy.deepcopy(base),
        node_states_json={"executed_graph_hash": "pinned", "executed_graph_hash_v2": True},
    )
    changed = copy.deepcopy(base)
    _receipt_node(changed)["data"]["receiptSignal"] = "multi_per_page"
    _receipt_node(changed)["data"]["tablePreset"] = "ap_table"

    assert clear_executed_graph_hash_if_topology_changed(run, changed) is False
    assert run.node_states_json.get("executed_graph_hash") == "pinned"


def test_assert_allows_execute_after_topology_hash_cleared():
    run = WorkflowRun(
        id="run-1",
        graph_json=_with_manager_review("AP"),
        node_states_json={},
    )
    assert_graph_unchanged_or_raise(run)


def test_store_executed_graph_hash_sets_v2_flag():
    base = build_default_graph("AP")
    run = WorkflowRun(id="run-1", graph_json=copy.deepcopy(base), node_states_json={})
    from app.graph.workflow_path import store_executed_graph_hash

    store_executed_graph_hash(run)
    assert run.node_states_json.get("executed_graph_hash_v2") is True

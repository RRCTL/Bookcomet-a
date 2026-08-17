from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.graph.default_graphs import build_default_graph
from app.graph.executor import run_workflow_graph, topo_node_order


def test_topo_order_default_ap_graph():
    g = build_default_graph("AP")
    ordered = topo_node_order(g)
    ids = [n["id"] for n in ordered]
    assert ids.index("files") < ids.index("vlm")
    assert ids.index("vlm") < ids.index("table")


@pytest.mark.asyncio
async def test_stop_after_table_skips_coa_and_save():
    invoked: list[str] = []

    async def track(_db, _run, node, _emit):
        invoked.append(str(node.get("id")))

    handlers = {
        "Files": track,
        "ModeConfig": track,
        "ReceiptStyle": track,
        "VLM_API": track,
        "TableReview": track,
        "CoADeploy": track,
        "SaveResult": track,
    }
    run = SimpleNamespace(
        id="run-1",
        processing_mode="AP",
        graph_json=build_default_graph("AP"),
        run_status="draft",
        node_states_json={},
    )
    db = MagicMock()

    await run_workflow_graph(db, run, handlers, stop_after="table")

    assert "coa" not in invoked
    assert "save" not in invoked
    assert invoked[-1] == "table"
    db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_start_at_save_runs_only_save():
    invoked: list[str] = []

    async def track(_db, _run, node, _emit):
        invoked.append(str(node.get("id")))

    handlers = {
        "Files": track,
        "ModeConfig": track,
        "ReceiptStyle": track,
        "VLM_API": track,
        "TableReview": track,
        "SaveResult": track,
    }
    run = SimpleNamespace(
        id="run-1",
        processing_mode="AP",
        graph_json=build_default_graph("AP"),
        run_status="awaiting_review",
        node_states_json={},
    )
    db = MagicMock()

    await run_workflow_graph(db, run, handlers, start_at="save")

    assert invoked == ["save"]


@pytest.mark.asyncio
async def test_start_at_missing_raises():
    run = SimpleNamespace(
        id="run-1",
        processing_mode="AP",
        graph_json=build_default_graph("AP"),
        run_status="awaiting_review",
        node_states_json={},
    )
    db = MagicMock()

    with pytest.raises(ValueError, match="start_at node not found"):
        await run_workflow_graph(db, run, {}, start_at="missing")


@pytest.mark.asyncio
async def test_node_handler_retries_from_node_data():
    g = build_default_graph("AP")
    for node in g["nodes"]:
        if node["id"] == "mode":
            node["data"]["maxRetries"] = 2
    attempts = 0

    async def flaky(_db, _run, node, _emit):
        nonlocal attempts
        if node.get("id") == "mode":
            attempts += 1
            if attempts < 2:
                raise ValueError("temporary")

    handlers = {
        "Files": flaky,
        "ModeConfig": flaky,
        "ReceiptStyle": flaky,
        "VLM_API": flaky,
        "TableReview": flaky,
    }
    run = SimpleNamespace(
        id="run-1",
        processing_mode="AP",
        graph_json=g,
        run_status="draft",
        node_states_json={},
    )
    db = MagicMock()

    await run_workflow_graph(db, run, handlers, stop_after="table")

    assert attempts == 2

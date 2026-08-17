import pytest

from app.graph.default_graphs import build_default_graph
from app.graph.graph_schema_v2 import (
    GRAPH_VERSION,
    edges_compatible,
    ensure_graph_v2,
    graph_version,
    normalize_graph_v2,
)
from app.graph.graph_utils import validate_graph_edges, validate_no_cycles


def test_default_graph_is_schema_v2():
    g = build_default_graph("AP")
    assert g["schemaVersion"] == GRAPH_VERSION
    assert graph_version(g) == GRAPH_VERSION


def test_cross_vlm_migrates_to_double_check_node():
    g = build_default_graph("AP")
    for n in g["nodes"]:
        if n.get("type") == "VLM_API":
            n["data"]["crossVlm"] = True
    g.pop("schemaVersion", None)
    out = normalize_graph_v2(g, "AP")
    assert any(n.get("type") == "VLMDoubleCheck" for n in out["nodes"])
    vlm = next(n for n in out["nodes"] if n.get("type") == "VLM_API")
    assert vlm["data"].get("crossVlm") is False
    assert out.get("graphV1Backup") is not None


def test_typed_edges_valid_on_default_ap():
    g = build_default_graph("AP")
    validate_graph_edges(g)
    validate_no_cycles(g)


def test_incompatible_edge_raises():
    g = build_default_graph("BANK")
    g["edges"].append(
        {
            "id": "bad",
            "source": "save",
            "target": "files",
            "sourceHandle": "out",
            "targetHandle": "in",
        }
    )
    with pytest.raises(ValueError, match="Incompatible edge"):
        validate_graph_edges(g)


def test_cycle_raises():
    g = build_default_graph("AR")
    g["edges"].append(
        {
            "id": "cycle",
            "source": "save",
            "target": "vlm",
            "sourceHandle": "out",
            "targetHandle": "in",
        }
    )
    with pytest.raises(ValueError, match="cycle"):
        validate_no_cycles(g)


def test_verified_ocr_to_table_review_compatible():
    assert edges_compatible("VLMDoubleCheck", "TableReview", "out", "in")


def test_ensure_graph_v2_idempotent():
    g = build_default_graph("BANK")
    once = ensure_graph_v2(g, "BANK")
    twice = ensure_graph_v2(once, "BANK")
    assert twice["schemaVersion"] == GRAPH_VERSION
    assert len(twice["nodes"]) == len(once["nodes"])

from types import SimpleNamespace

from app.graph.default_graphs import build_default_graph
from app.graph.graph_migrate import migrate_graph_json
from app.graph.graph_schema_v2 import graph_version


def test_migrate_graph_json_adds_schema_version():
    raw = {"nodes": [], "edges": [], "processingMode": "AR"}
    raw["nodes"] = build_default_graph("AR")["nodes"]
    raw["edges"] = build_default_graph("AR")["edges"]
    raw.pop("schemaVersion", None)
    out = migrate_graph_json(raw, "AR")
    assert graph_version(out) >= 2
    assert out["schemaVersion"] == 2


def test_v1_cross_vlm_backup_preserved():
    g = build_default_graph("AP")
    for n in g["nodes"]:
        if n.get("type") == "VLM_API":
            n["data"]["crossVlm"] = True
    legacy = {"nodes": g["nodes"], "edges": g["edges"], "processingMode": "AP"}
    out = migrate_graph_json(legacy, "AP")
    assert out.get("graphV1Backup") is not None
    assert any(n.get("type") == "VLMDoubleCheck" for n in out["nodes"])

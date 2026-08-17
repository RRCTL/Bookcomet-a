from app.graph.nodes.registry import NODE_CLASS_MAPPINGS


def test_node_registry_has_core_types():
    for ntype in ("Files", "ModeConfig", "VLM_API", "TableReview", "CoADeploy", "SaveResult"):
        assert ntype in NODE_CLASS_MAPPINGS

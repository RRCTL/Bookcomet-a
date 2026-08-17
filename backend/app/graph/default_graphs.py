from __future__ import annotations

from typing import Any

from app.graph.graph_schema_v2 import ensure_graph_v2


def _edge(
    source: str,
    target: str,
    *,
    source_handle: str = "out",
    target_handle: str = "in",
) -> dict[str, str]:
    return {
        "id": f"{source}-{target}",
        "source": source,
        "target": target,
        "sourceHandle": source_handle,
        "targetHandle": target_handle,
    }


def _node(
    nid: str,
    ntype: str,
    x: float,
    y: float,
    *,
    label: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {"label": label or ntype, "nodeType": ntype}
    if extra:
        data.update(extra)
    return {
        "id": nid,
        "type": ntype,
        "position": {"x": x, "y": y},
        "data": data,
    }


def build_default_graph(processing_mode: str) -> dict[str, Any]:
    mode = (processing_mode or "AR").upper()
    nodes: list[dict[str, Any]] = [
        _node("files", "Files", 40, 120, label="Files"),
        _node("mode", "ModeConfig", 280, 120, label="Mode", extra={"processingMode": mode}),
        _node(
            "vlm",
            "VLM_API",
            760,
            120,
            label="VLM",
            extra={"provider": "Qwen", "model": None, "crossVlm": False, "promptPreset": "default"},
        ),
        _node("table", "TableReview", 1040, 120, label="Table Review", extra={"expanded": False}),
        _node("save", "SaveResult", 1320, 120, label="Save"),
    ]
    edges = [
        _edge("files", "mode"),
        _edge("mode", "vlm"),
        _edge("vlm", "table"),
        _edge("table", "save"),
    ]
    if mode in ("AR", "AP"):
        nodes.insert(
            2,
            _node(
                "receipt",
                "ReceiptStyle",
                520,
                120,
                label="Receipt Style",
                extra={
                    "receiptSignal": "guess",
                    "tablePreset": "default",
                },
            ),
        )
        edges = [
            _edge("files", "mode"),
            _edge("mode", "receipt"),
            _edge("receipt", "vlm"),
            _edge("vlm", "table"),
            _edge("table", "save"),
        ]
    base = {"nodes": nodes, "edges": edges, "processingMode": mode}
    return ensure_graph_v2(base, mode)

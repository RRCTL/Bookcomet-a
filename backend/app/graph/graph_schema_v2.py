"""Workflow graph schema V2: versioned graphs, node specs, normalization."""

from __future__ import annotations

import copy
from typing import Any

from app.core.config import default_workflow_provider, normalize_workflow_provider_label, workflow_model_options, workflow_provider_options

GRAPH_VERSION = 2

# Typed I/O labels for edge validation
IO_FILE_BATCH = "FILE_BATCH"
IO_FILE_ITEM = "FILE_ITEM"
IO_OCR_CONTEXT = "OCR_CONTEXT"
IO_OCR_RESULT = "OCR_RESULT"
IO_VERIFIED_OCR = "VERIFIED_OCR_RESULT"
IO_OCR_PROPOSAL = "OCR_PROPOSAL"
IO_OCR_PROPOSAL_POOL = "OCR_PROPOSAL_POOL"
IO_JUDGE_REPORT = "JUDGE_REPORT"
IO_VERIFICATION_REPORT = "VERIFICATION_REPORT"
IO_SELECTED_OCR_RESULT = "SELECTED_OCR_RESULT"
IO_APPROVED_TABLE = "APPROVED_TABLE"
IO_SAVED_PACKAGE = "SAVED_PACKAGE"
IO_ANY = "ANY"

ALL_MODES = ("AR", "AP", "BANK", "OTHER", "RECON")
OCR_MODES = ALL_MODES
AR_AP_MODES = ("AR", "AP")


def _limit_params() -> dict[str, dict[str, Any]]:
    return {
        "maxRetries": {"type": "number", "default": 2, "min": 0, "max": 5},
        "timeoutMs": {"type": "number", "default": 120000, "min": 1000},
        "maxCost": {"type": "number", "default": None, "min": 0, "nullable": True},
        "maxVlmCallsPerDocument": {"type": "number", "default": 3, "min": 1, "max": 8},
        "bypassLimits": {"type": "boolean", "default": False},
    }


def _vlm_provider_params(default_provider: str | None = None) -> dict[str, dict[str, Any]]:
    provider_default = default_provider or default_workflow_provider()
    return {
        "provider": {
            "type": "string",
            "default": provider_default,
            "options": workflow_provider_options(),
        },
        "model": {
            "type": "string",
            "default": None,
            "nullable": True,
            "options": workflow_model_options(),
        },
    }


def _spec(
    *,
    label: str,
    category: str,
    inputs: dict[str, str],
    outputs: dict[str, str],
    modes: tuple[str, ...],
    handler_key: str,
    params: dict[str, dict[str, Any]] | None = None,
    skill_attachable: bool = False,
    description: str = "",
    protected: bool = False,
) -> dict[str, Any]:
    return {
        "label": label,
        "category": category,
        "description": description,
        "inputs": inputs,
        "outputs": outputs,
        "modes": modes,
        "handlerKey": handler_key,
        "params": params or {},
        "skillAttachable": skill_attachable,
        "protected": protected,
    }


NODE_SPECS: dict[str, dict[str, Any]] = {
    "Files": _spec(
        label="Files",
        category="Source",
        inputs={},
        outputs={"out": IO_FILE_BATCH},
        modes=OCR_MODES,
        handler_key="Files",
        protected=True,
        description="Loads uploaded files into the workflow.",
    ),
    "ModeConfig": _spec(
        label="Mode",
        category="Context",
        inputs={"in": IO_FILE_BATCH},
        outputs={"out": IO_OCR_CONTEXT},
        modes=OCR_MODES,
        handler_key="ModeConfig",
        protected=True,
        description="Applies the run processing mode.",
    ),
    "ReceiptStyle": _spec(
        label="Receipt Style",
        category="Context",
        inputs={"in": IO_OCR_CONTEXT},
        outputs={"out": IO_OCR_CONTEXT},
        modes=AR_AP_MODES,
        handler_key="ReceiptStyle",
        params={
            "receiptSignal": {"type": "string", "default": "guess"},
            "tablePreset": {"type": "string", "default": "default"},
        },
        description="Sets AR/AP receipt layout and table extraction preset.",
    ),
    "VLM_API": _spec(
        label="VLM API",
        category="VLM",
        inputs={"in": IO_OCR_CONTEXT},
        outputs={"out": IO_OCR_RESULT},
        modes=OCR_MODES,
        handler_key="VLM_API",
        params={
            **_vlm_provider_params("Qwen"),
            "crossVlm": {"type": "boolean", "default": False},
            "promptPreset": {"type": "string", "default": "default"},
            **_limit_params(),
        },
        skill_attachable=True,
        description="Runs the existing OCR VLM extraction pipeline.",
    ),
    "If": _spec(
        label="If",
        category="Routing",
        inputs={"in": IO_FILE_ITEM},
        outputs={"true": IO_FILE_ITEM, "false": IO_FILE_ITEM},
        modes=OCR_MODES,
        handler_key="If",
        params={"condition": {"type": "string", "default": "needs_double_check"}},
        description="Routes files by a boolean workflow condition.",
    ),
    "Switch": _spec(
        label="Switch",
        category="Routing",
        inputs={"in": IO_FILE_ITEM},
        outputs={"out0": IO_FILE_ITEM, "out1": IO_FILE_ITEM, "default": IO_FILE_ITEM},
        modes=OCR_MODES,
        handler_key="Switch",
        params={"switchOn": {"type": "string", "default": "file_status"}},
        description="Routes files across multiple branches.",
    ),
    "VLMDoubleCheck": _spec(
        label="VLM Double Check",
        category="VLM",
        inputs={"in": IO_OCR_RESULT},
        outputs={"out": IO_VERIFIED_OCR},
        modes=OCR_MODES,
        handler_key="VLMDoubleCheck",
        params={
            **_vlm_provider_params(),
            "mergePolicy": {"type": "string", "default": "cross_vlm"},
            "enabled": {"type": "boolean", "default": True},
            **_limit_params(),
        },
        skill_attachable=True,
        description="Runs the current second-pass verification node.",
    ),
    "VLMProposer": _spec(
        label="VLM Proposer",
        category="Plugin",
        inputs={"in": IO_OCR_CONTEXT},
        outputs={"out": IO_OCR_PROPOSAL},
        modes=OCR_MODES,
        handler_key="VLMProposer",
        params={
            **_vlm_provider_params("Qwen"),
            "proposalName": {"type": "string", "default": "Proposal"},
            "skillKey": {"type": "string", "default": None, "nullable": True},
            **_limit_params(),
        },
        skill_attachable=True,
        description="Runs one VLM extraction as a proposal for later judging or voting.",
    ),
    "ProposalPoolJoin": _spec(
        label="Proposal Pool Join",
        category="Plugin",
        inputs={"in": IO_OCR_PROPOSAL},
        outputs={"out": IO_OCR_PROPOSAL_POOL},
        modes=OCR_MODES,
        handler_key="ProposalPoolJoin",
        description="Combines proposal node outputs into a proposal pool.",
    ),
    "VLMJudge": _spec(
        label="VLM Judge",
        category="Plugin",
        inputs={"in": IO_OCR_PROPOSAL_POOL},
        outputs={"out": IO_JUDGE_REPORT},
        modes=OCR_MODES,
        handler_key="VLMJudge",
        params={
            **_vlm_provider_params(),
            "skillKey": {"type": "string", "default": "vlm_judge_equivalence"},
            **_limit_params(),
        },
        skill_attachable=True,
        description="Judges whether proposal results are equivalent at document level.",
    ),
    "VoteSelector": _spec(
        label="Vote Selector",
        category="Plugin",
        inputs={"in": IO_JUDGE_REPORT},
        outputs={"out": IO_SELECTED_OCR_RESULT},
        modes=OCR_MODES,
        handler_key="VoteSelector",
        params={
            "policy": {"type": "string", "default": "majority"},
            "skillKey": {"type": "string", "default": "majority_vote"},
        },
        skill_attachable=True,
        description="Selects the final proposal group using the configured vote policy.",
    ),
    "ManagerReview": _spec(
        label="Manager Review",
        category="Plugin",
        inputs={"in": IO_SELECTED_OCR_RESULT},
        outputs={"out": IO_VERIFICATION_REPORT},
        modes=OCR_MODES,
        handler_key="ManagerReview",
        params={
            **_vlm_provider_params(),
            "skillKey": {"type": "string", "default": "manager_review"},
            "retryOnFail": {"type": "boolean", "default": True},
            **_limit_params(),
        },
        skill_attachable=True,
        description="Runs a manager-style VLM check and can request a retry with feedback.",
    ),
    "ConditionRouter": _spec(
        label="Condition Router",
        category="Plugin",
        inputs={"in": IO_VERIFICATION_REPORT},
        outputs={"pass": IO_VERIFICATION_REPORT, "retry": IO_VERIFICATION_REPORT, "fail": IO_VERIFICATION_REPORT},
        modes=OCR_MODES,
        handler_key="ConditionRouter",
        params={"condition": {"type": "string", "default": "manager_status"}},
        skill_attachable=True,
        description="Routes verification reports by structured status.",
    ),
    "ExternalApiCall": _spec(
        label="External API Call",
        category="Plugin",
        inputs={"in": IO_ANY},
        outputs={"out": IO_ANY},
        modes=ALL_MODES,
        handler_key="ExternalApiCall",
        params={
            "endpointEnvKey": {"type": "string", "default": None, "nullable": True},
            "method": {"type": "string", "default": "POST"},
            "dangerAcknowledged": {"type": "boolean", "default": False},
            **_limit_params(),
        },
        skill_attachable=True,
        description="Calls a user-configured external API. This may send document data outside the app.",
    ),
    "MergeResult": _spec(
        label="Merge Result",
        category="Plugin",
        inputs={"in": IO_VERIFICATION_REPORT},
        outputs={"out": IO_OCR_RESULT},
        modes=OCR_MODES,
        handler_key="MergeResult",
        params={"policy": {"type": "string", "default": "selected_result"}},
        description="Converts the selected or verified result back into the OCR table result.",
    ),
    "TableReview": _spec(
        label="Table Review",
        category="Review",
        inputs={"in": IO_OCR_RESULT},
        outputs={"out": IO_APPROVED_TABLE},
        modes=OCR_MODES,
        handler_key="TableReview",
        protected=True,
        description="Shows the selected OCR result for table review.",
    ),
    "CoADeploy": _spec(
        label="CoA Deploy",
        category="Accounting",
        inputs={"in": IO_APPROVED_TABLE},
        outputs={"out": IO_APPROVED_TABLE},
        modes=OCR_MODES,
        handler_key="CoADeploy",
        protected=True,
        description="Deploys approved chart-of-accounts changes.",
    ),
    "SaveResult": _spec(
        label="Save",
        category="Storage",
        inputs={"in": IO_APPROVED_TABLE},
        outputs={"out": IO_SAVED_PACKAGE},
        modes=OCR_MODES,
        handler_key="SaveResult",
        protected=True,
        description="Saves the approved workflow package.",
    ),
}

# Source output type -> allowed target input types
_EDGE_COMPAT: dict[str, frozenset[str]] = {
    IO_FILE_BATCH: frozenset({IO_FILE_BATCH, IO_OCR_CONTEXT}),
    IO_FILE_ITEM: frozenset({IO_FILE_ITEM, IO_OCR_RESULT}),
    IO_OCR_CONTEXT: frozenset({IO_OCR_CONTEXT}),
    IO_OCR_RESULT: frozenset({IO_OCR_RESULT, IO_VERIFIED_OCR, IO_APPROVED_TABLE, IO_SELECTED_OCR_RESULT}),
    IO_OCR_PROPOSAL: frozenset({IO_OCR_PROPOSAL, IO_OCR_PROPOSAL_POOL}),
    IO_OCR_PROPOSAL_POOL: frozenset({IO_OCR_PROPOSAL_POOL, IO_JUDGE_REPORT}),
    IO_JUDGE_REPORT: frozenset({IO_JUDGE_REPORT, IO_SELECTED_OCR_RESULT}),
    IO_SELECTED_OCR_RESULT: frozenset({IO_SELECTED_OCR_RESULT, IO_VERIFICATION_REPORT, IO_OCR_RESULT}),
    IO_VERIFICATION_REPORT: frozenset({IO_VERIFICATION_REPORT, IO_OCR_RESULT}),
    IO_VERIFIED_OCR: frozenset({IO_VERIFIED_OCR, IO_OCR_RESULT, IO_APPROVED_TABLE}),
    IO_APPROVED_TABLE: frozenset({IO_APPROVED_TABLE, IO_SAVED_PACKAGE}),
    IO_SAVED_PACKAGE: frozenset({IO_SAVED_PACKAGE}),
    IO_ANY: frozenset(
        {
            IO_FILE_BATCH,
            IO_FILE_ITEM,
            IO_OCR_CONTEXT,
            IO_OCR_RESULT,
            IO_OCR_PROPOSAL,
            IO_OCR_PROPOSAL_POOL,
            IO_JUDGE_REPORT,
            IO_VERIFICATION_REPORT,
            IO_SELECTED_OCR_RESULT,
            IO_VERIFIED_OCR,
            IO_APPROVED_TABLE,
            IO_SAVED_PACKAGE,
            IO_ANY,
        }
    ),
}


def graph_version(graph_json: dict[str, Any] | None) -> int:
    if not isinstance(graph_json, dict):
        return 1
    v = graph_json.get("schemaVersion")
    if isinstance(v, int) and v >= 2:
        return v
    return 1


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


def _default_vlm_params(mode: str) -> dict[str, Any]:
    return {
        "provider": "Qwen",
        "model": None,
        "crossVlm": False,
        "promptPreset": "default",
    }


def _default_double_check_params(mode: str) -> dict[str, Any]:
    return {
        "provider": default_workflow_provider(),
        "model": None,
        "mergePolicy": "cross_vlm",
        "enabled": True,
    }


def _migrate_graph_provider_labels(nodes: list[dict[str, Any]]) -> None:
    for node in nodes:
        data = node.get("data")
        if not isinstance(data, dict):
            continue
        if "provider" in data:
            data["provider"] = normalize_workflow_provider_label(str(data.get("provider") or ""))


def normalize_graph_v2(graph_json: dict[str, Any] | None, processing_mode: str = "AR") -> dict[str, Any]:
    """Normalize graph to schema V2; migrate crossVlm into VLMDoubleCheck topology when needed."""
    if not isinstance(graph_json, dict):
        from app.graph.default_graphs import build_default_graph

        g = build_default_graph(processing_mode)
        return normalize_graph_v2(g, processing_mode)

    if graph_version(graph_json) >= GRAPH_VERSION and graph_json.get("schemaVersion") == GRAPH_VERSION:
        out = copy.deepcopy(graph_json)
        out.setdefault("schemaVersion", GRAPH_VERSION)
        nodes = out.get("nodes")
        if isinstance(nodes, list):
            _migrate_graph_provider_labels(nodes)
        return out

    backup = copy.deepcopy(graph_json)
    mode = (processing_mode or graph_json.get("processingMode") or "AR").upper()
    nodes = list(graph_json.get("nodes") or [])
    edges = list(graph_json.get("edges") or [])

    vlm_node = next((n for n in nodes if n.get("type") == "VLM_API"), None)
    cross = False
    if vlm_node and isinstance(vlm_node.get("data"), dict):
        cross = bool(vlm_node["data"].get("crossVlm"))
        vlm_node["data"].setdefault("provider", "Qwen")
        vlm_node["data"].setdefault("model", None)
        vlm_node["data"].setdefault("promptPreset", "default")

    has_double = any(n.get("type") == "VLMDoubleCheck" for n in nodes)
    table_id = next((n["id"] for n in nodes if n.get("type") == "TableReview"), "table")
    vlm_id = next((n["id"] for n in nodes if n.get("type") == "VLM_API"), "vlm")

    if cross and not has_double:
        dc_id = "vlm_double_check"
        dc_node = {
            "id": dc_id,
            "type": "VLMDoubleCheck",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "VLM Double Check",
                "nodeType": "VLMDoubleCheck",
                **_default_double_check_params(mode),
            },
        }
        nodes.append(dc_node)
        edges = [e for e in edges if not (e.get("source") == vlm_id and e.get("target") == table_id)]
        edges.append(_edge(vlm_id, dc_id))
        edges.append(_edge(dc_id, table_id))
        if vlm_node and isinstance(vlm_node.get("data"), dict):
            vlm_node["data"]["crossVlm"] = False

    for n in nodes:
        ntype = str(n.get("type") or "")
        spec = NODE_SPECS.get(ntype)
        if spec:
            data = n.setdefault("data", {})
            data.setdefault("nodeType", ntype)
            data.setdefault("modeCompatibility", list(spec["modes"]))

    _migrate_graph_provider_labels(nodes)

    return {
        "schemaVersion": GRAPH_VERSION,
        "processingMode": mode,
        "nodes": nodes,
        "edges": edges,
        "graphV1Backup": backup,
    }


def ensure_graph_v2(graph_json: dict[str, Any] | None, processing_mode: str = "AR") -> dict[str, Any]:
    return normalize_graph_v2(graph_json, processing_mode)


def _param_defaults(params: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {key: spec.get("default") for key, spec in params.items() if "default" in spec}


def node_catalog(processing_mode: str | None = None) -> list[dict[str, Any]]:
    mode = (processing_mode or "").upper()
    out: list[dict[str, Any]] = []
    for node_type, spec in NODE_SPECS.items():
        modes = list(spec.get("modes") or ())
        if mode and mode not in modes:
            continue
        params = spec.get("params") if isinstance(spec.get("params"), dict) else {}
        out.append(
            {
                "type": node_type,
                "label": spec.get("label") or node_type,
                "category": spec.get("category") or "Workflow",
                "description": spec.get("description") or "",
                "inputs": dict(spec.get("inputs") or {}),
                "outputs": dict(spec.get("outputs") or {}),
                "modes": modes,
                "params": copy.deepcopy(params),
                "defaults": _param_defaults(params),
                "handlerKey": spec.get("handlerKey") or node_type,
                "skillAttachable": bool(spec.get("skillAttachable")),
                "protected": bool(spec.get("protected")),
            }
        )
    return out


def edge_output_type(node_type: str, source_handle: str | None) -> str:
    spec = NODE_SPECS.get(node_type, {})
    outputs = spec.get("outputs") or {}
    handle = source_handle or "out"
    return str(outputs.get(handle) or outputs.get("out") or IO_ANY)


def edge_input_type(node_type: str, target_handle: str | None) -> str:
    spec = NODE_SPECS.get(node_type, {})
    inputs = spec.get("inputs") or {}
    if not inputs:
        return "NONE"
    handle = target_handle or "in"
    return str(inputs.get(handle) or IO_ANY)


def edges_compatible(source_type: str, target_type: str, source_handle: str | None, target_handle: str | None) -> bool:
    out_t = edge_output_type(source_type, source_handle)
    in_t = edge_input_type(target_type, target_handle)
    if in_t == "NONE":
        return False
    if out_t == IO_ANY or in_t == IO_ANY:
        return True
    if out_t == IO_VERIFIED_OCR and in_t == IO_OCR_RESULT:
        return True
    allowed = _EDGE_COMPAT.get(out_t, frozenset())
    return in_t in allowed or IO_ANY in allowed

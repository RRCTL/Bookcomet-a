"""Workflow node handlers — wrap existing WorkflowService steps."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import default_workflow_provider, resolve_gateway, settings
from app.services.extraction_validation import clean_manager_ar_ap_rows
from app.graph.branch_routing import (
    evaluate_if_condition,
    evaluate_switch_on,
    filter_run_files_for_node,
    set_file_routes,
    should_skip_branch_node,
)
from app.graph.executor import EmitFn, NodeHandler
from app.graph.node_runtime import load_latest_node_payload, record_node_execution
from app.graph.graph_utils import node_data
from app.graph.workflow_skills import get_or_create_skill
from app.graph.workflow_path import store_executed_graph_hash
from app.graph.workflow_service import (
    WorkflowService,
    _apply_vlm_ocr_states,
    _finalize_run_after_vlm,
    _set_node_state,
    row_file_ids_for_rows,
)
from app.models.workflow import WorkflowRun, WorkflowRunFile
from app.services.ai_chat_client import deploy_chat_client
from app.services.ai_enhance_client import AiEnhanceClient


def _incoming_node_ids(run: WorkflowRun, node_id: str) -> list[str]:
    graph = run.graph_json if isinstance(run.graph_json, dict) else {}
    edges = graph.get("edges") if isinstance(graph.get("edges"), list) else []
    return [
        str(edge.get("source"))
        for edge in edges
        if isinstance(edge, dict) and str(edge.get("target") or "") == node_id and edge.get("source")
    ]


def _incoming_payloads(db: Session, run: WorkflowRun, node_id: str) -> list[dict[str, Any]]:
    return [payload for _, payload in _incoming_payloads_by_source(db, run, node_id)]


def _incoming_payloads_by_source(
    db: Session,
    run: WorkflowRun,
    node_id: str,
) -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    for source_id in _incoming_node_ids(run, node_id):
        payload = load_latest_node_payload(db, run.id, source_id)
        if isinstance(payload, dict):
            items.append((source_id, payload))
    return items


def _proposal_from_incoming(source_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    merged = payload.get("merged_ocr")
    if not isinstance(merged, list) or len(merged) == 0:
        return None
    proposal_id = str(payload.get("proposal_node_id") or source_id)
    return {**payload, "proposal_node_id": proposal_id, "merged_ocr": merged}


def _completed_run_files(db: Session, run: WorkflowRun) -> list[WorkflowRunFile]:
    return (
        db.query(WorkflowRunFile)
        .filter(WorkflowRunFile.run_id == run.id)
        .all()
    )


def _proposal_rows(proposal: dict[str, Any]) -> list[dict[str, Any]]:
    rows = proposal.get("merged_ocr")
    return rows if isinstance(rows, list) else []


_MANAGER_LLM_ROW_KEYS = frozenset(
    {
        "id_number",
        "matched_id",
        "date",
        "due_date",
        "invoice_number",
        "vendor_tax_id",
        "tax_amount",
        "payment_status",
        "source_file",
        "file_position",
        "_page",
        "page",
        "transaction_type",
        "amount",
        "total",
        "currency",
        "payer",
        "payee",
        "vendor",
        "vendor_name",
        "bank",
        "account_code",
        "category",
        "memo",
        "confidence",
        "needs_review",
        "validation_flags",
    }
)


def _trim_manager_llm_row(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {k: row[k] for k in _MANAGER_LLM_ROW_KEYS if k in row and row[k] not in (None, "")}


def _trim_manager_llm_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [_trim_manager_llm_row(r) for r in rows if isinstance(r, dict)]


def _slim_manager_proposal(proposal: dict[str, Any]) -> dict[str, Any]:
    return {
        "proposal_node_id": proposal.get("proposal_node_id"),
        "merged_ocr": _trim_manager_llm_rows(_proposal_rows(proposal)),
    }


def _slim_manager_input_payload(input_payload: dict[str, Any], *, tie_break: bool) -> dict[str, Any]:
    slim = dict(input_payload)
    if tie_break:
        proposals = slim.get("proposals")
        if isinstance(proposals, list):
            slim["proposals"] = [
                _slim_manager_proposal(p) for p in proposals if isinstance(p, dict)
            ]
    else:
        selected_rows = slim.get("selected_rows")
        if isinstance(selected_rows, list):
            slim["selected_rows"] = _trim_manager_llm_rows(selected_rows)
        selected_group = slim.get("selected_group")
        if isinstance(selected_group, dict):
            slim["selected_group"] = {
                k: v for k, v in selected_group.items() if k != "rows"
            }
    return slim


def _proposals_from_payloads(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for payload in payloads:
        raw = payload.get("proposals")
        if isinstance(raw, list):
            return [p for p in raw if isinstance(p, dict)]
    return []


def _pool_proposals_for_manager(db: Session, run: WorkflowRun, node_id: str) -> list[dict[str, Any]]:
    """Load proposal pool output when vote payload does not carry proposals."""
    for source_id in _incoming_node_ids(run, node_id):
        for upstream_id in _incoming_node_ids(run, source_id):
            payload = load_latest_node_payload(db, run.id, upstream_id)
            if isinstance(payload, dict) and isinstance(payload.get("proposals"), list):
                return [p for p in payload["proposals"] if isinstance(p, dict)]
    return []


def _rows_key(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_json_text(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = AiEnhanceClient._extract_json_from_text(text)
    return parsed if isinstance(parsed, dict) else {"raw_text": text}


def _node_skill_markdown(db: Session, run: WorkflowRun, data: dict[str, Any]) -> str | None:
    skill_key = data.get("skillKey")
    if not skill_key:
        return None
    row = get_or_create_skill(db, run.company_id, run.processing_mode, str(skill_key))
    return row.generated_markdown


async def _call_workflow_vlm(
    db: Session,
    run: WorkflowRun,
    data: dict[str, Any],
    *,
    node_type: str,
    input_payload: dict[str, Any],
    output_contract: str,
) -> dict[str, Any]:
    provider = str(data.get("provider") or "Qwen")
    model = str(data.get("model") or "").strip() or None
    skill_markdown = _node_skill_markdown(db, run, data)
    system_prompt = "\n\n".join(
        part
        for part in [
            skill_markdown,
            f"You are running the {node_type} workflow node.",
            "Use only the provided workflow JSON input. Return strict JSON only.",
            output_contract,
        ]
        if part
    )
    user_text = json.dumps(input_payload, ensure_ascii=False, sort_keys=True)

    # The selected model drives the gateway; the legacy provider name is only a
    # fallback when no model is chosen.
    if resolve_gateway(model, provider) == "ai_enhance":
        service = AiEnhanceClient(
            api_key=settings.ai_enhance_api_key,
            base_url=settings.ai_enhance_api_base,
            default_model=model or settings.ai_enhance_model,
        )
        result = await asyncio.to_thread(
            service.extract_fields_with_prompt,
            user_text,
            system_prompt,
            model,
            2048,
        )
        return {
            "provider": provider,
            "model": result.model,
            "data": result.data,
            "raw": result.raw,
            "elapsed_time": result.elapsed_time,
        }

    content, raw_response = await asyncio.to_thread(
        deploy_chat_client.complete,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text},
        ],
        model,
    )
    return {
        "provider": provider,
        "model": model or settings.deploy_model,
        "data": _parse_json_text(content),
        "raw": content,
        "raw_response": raw_response,
    }


def _workflow_states(run: WorkflowRun) -> dict[str, Any]:
    raw = run.node_states_json
    return dict(raw) if isinstance(raw, dict) else {}


def _set_workflow_error(run: WorkflowRun, message: str) -> None:
    states = _workflow_states(run)
    states["workflow_error"] = message
    run.node_states_json = states


_DETAIL_TEXT_MAX = 220


def _clip_detail_text(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) <= _DETAIL_TEXT_MAX:
        return text
    return text[: _DETAIL_TEXT_MAX - 1] + "…"


def _feedback_from_rows(rows: Any) -> str:
    if not isinstance(rows, list):
        return ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key in ("memo", "reason", "particulars", "description"):
            text = _clip_detail_text(row.get(key))
            if text:
                return text
    return ""


def _node_detail_summary(**fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None or value == "":
            continue
        if key in ("feedback", "reason", "manager_feedback", "error"):
            text = _clip_detail_text(value)
            if text:
                out[key if key != "manager_feedback" else "feedback"] = text
        else:
            out[key] = value
    return out


def _append_workflow_warning(run: WorkflowRun, message: str) -> None:
    states = _workflow_states(run)
    warnings = states.get("workflow_warnings")
    items = list(warnings) if isinstance(warnings, list) else []
    if message not in items:
        items.append(message)
    states["workflow_warnings"] = items
    run.node_states_json = states


def _record_plugin_payload(
    db: Session,
    run: WorkflowRun,
    node: dict[str, Any],
    payload: dict[str, Any],
    *,
    status: str = "completed",
) -> None:
    node_id = str(node.get("id") or node.get("type") or "plugin")
    node_type = str(node.get("type") or "Plugin")
    record_node_execution(
        db,
        run,
        node_id=node_id,
        node_type=node_type,
        status=status,
        payload=payload,
        provider=str(payload.get("provider")) if payload.get("provider") else None,
        model=str(payload.get("model")) if payload.get("model") else None,
    )
    _set_node_state(run, node_id, status, payload.get("summary"))
    db.commit()


async def files_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    _processable, run_files = WorkflowService.prepare_run_files_for_execute(db, run)
    node_id = str(node.get("id") or "files")
    _set_node_state(run, node_id, "completed", {"file_count": len(run_files)})
    await WorkflowService.record_files_node_output(db, run, node_id, run_files)


async def mode_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    node_id = str(node.get("id") or "mode")
    _set_node_state(run, node_id, "completed", {"processing_mode": run.processing_mode})
    db.commit()


async def receipt_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    node_id = str(node.get("id") or "receipt")
    _set_node_state(run, node_id, "completed", None)
    db.commit()


async def vlm_node(db: Session, run: WorkflowRun, node: dict[str, Any], emit: EmitFn | None) -> None:
    node_id = str(node.get("id") or "vlm")
    run_files = (
        db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
    )
    if should_skip_branch_node(run, node, run_files):
        _set_node_state(run, node_id, "skipped", {"reason": "no_files_on_branch"})
        db.commit()
        return
    await WorkflowService.execute_vlm_stage(db, run, node=node, emit=emit)


async def if_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    node_id = str(node.get("id") or "if")
    data = node_data(node)
    condition = str(data.get("condition") or "needs_double_check")
    run_files = (
        db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
    )
    true_ids, false_ids = evaluate_if_condition(condition, run_files)
    set_file_routes(run, node_id, {"true": true_ids, "false": false_ids})
    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    states["branch_condition"] = condition
    states["branch_node_id"] = node_id
    run.node_states_json = states
    _set_node_state(
        run,
        node_id,
        "completed",
        {"condition": condition, "true_count": len(true_ids), "false_count": len(false_ids)},
    )
    db.commit()


async def switch_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    node_id = str(node.get("id") or "switch")
    data = node_data(node)
    mode_key = str(data.get("switchOn") or "file_status")
    run_files = (
        db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
    )
    routes = evaluate_switch_on(mode_key, run_files)
    set_file_routes(run, node_id, routes)
    states = dict(run.node_states_json) if isinstance(run.node_states_json, dict) else {}
    states["switch_on"] = mode_key
    states["switch_node_id"] = node_id
    run.node_states_json = states
    _set_node_state(
        run,
        node_id,
        "completed",
        {
            "switchOn": mode_key,
            "out0_count": len(routes.get("out0", [])),
            "out1_count": len(routes.get("out1", [])),
            "default_count": len(routes.get("default", [])),
        },
    )
    db.commit()


async def vlm_double_check_node(db: Session, run: WorkflowRun, node: dict[str, Any], emit: EmitFn | None) -> None:
    run_files = (
        db.query(WorkflowRunFile).filter(WorkflowRunFile.run_id == run.id).all()
    )
    if should_skip_branch_node(run, node, run_files):
        node_id = str(node.get("id") or "vlm_double_check")
        _set_node_state(run, node_id, "skipped", {"reason": "no_files_on_branch"})
        db.commit()
        return
    await WorkflowService.execute_double_check_stage(db, run, node=node, emit=emit)


async def vlm_proposer_node(db: Session, run: WorkflowRun, node: dict[str, Any], emit: EmitFn | None) -> None:
    node_id = str(node.get("id") or "vlm_proposer")
    run_files = _completed_run_files(db, run)
    if should_skip_branch_node(run, node, run_files):
        _set_node_state(run, node_id, "skipped", {"reason": "no_files_on_branch"})
        db.commit()
        return

    prior_by_file = {rf.task_file_id: rf.result_summary_json for rf in run_files}
    data = node_data(node)
    try:
        summary = await WorkflowService.execute_vlm_stage(
            db,
            run,
            node=node,
            emit=emit,
            finalize_table=False,
            allow_reprocess=True,
        )
    except Exception as exc:
        _set_node_state(run, node_id, "failed", {"error": str(exc)[:200]})
        db.commit()
        return

    snapshots = summary.get("proposal_snapshots") if isinstance(summary.get("proposal_snapshots"), list) else []
    merged = summary.get("merged_ocr") if isinstance(summary.get("merged_ocr"), list) else []
    capped_count = int(summary.get("capped_count") or 0)

    for rf in _completed_run_files(db, run):
        prior = prior_by_file.get(rf.task_file_id)
        rf.result_summary_json = prior
    db.commit()

    if not merged and capped_count > 0:
        _set_node_state(run, node_id, "failed", {"reason": "vlm_cap_reached", "capped": capped_count})
        db.commit()
        return

    proposal_files = []
    for snap in snapshots:
        if not isinstance(snap, dict):
            continue
        proposal_files.append(
            {
                "task_file_id": snap.get("task_file_id"),
                "result_summary_json": snap.get("result_summary_json"),
            }
        )

    payload = {
        "proposal_node_id": node_id,
        "proposal_name": data.get("proposalName") or data.get("label") or node_id,
        "provider": data.get("provider"),
        "model": data.get("model"),
        "skill_key": data.get("skillKey"),
        "merged_ocr": merged,
        "files": proposal_files,
        "summary": _node_detail_summary(
            row_count=len(merged),
            capped=capped_count,
            feedback=_feedback_from_rows(merged),
        ),
    }
    _record_plugin_payload(db, run, node, payload)


async def proposal_pool_join_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    proposals: list[dict[str, Any]] = []
    for source_id, payload in _incoming_payloads_by_source(db, run, str(node.get("id") or "")):
        if isinstance(payload.get("proposals"), list):
            proposals.extend(p for p in payload["proposals"] if isinstance(p, dict))
            continue
        proposal = _proposal_from_incoming(source_id, payload)
        if proposal:
            proposals.append(proposal)
    if not proposals:
        _set_workflow_error(run, "No VLM proposals completed. At least one proposer must succeed.")
        raise ValueError("No VLM proposals completed for vote workflow.")
    total_proposers = sum(
        1
        for n in (run.graph_json or {}).get("nodes", [])
        if isinstance(n, dict) and n.get("type") == "VLMProposer"
    )
    if total_proposers and len(proposals) < total_proposers:
        _append_workflow_warning(
            run,
            f"{len(proposals)} of {total_proposers} proposers completed",
        )
    _record_plugin_payload(
        db,
        run,
        node,
        {
            "proposals": proposals,
            "summary": {"proposal_count": len(proposals)},
        },
    )


async def vlm_judge_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    incoming = _incoming_payloads(db, run, str(node.get("id") or ""))
    proposals = incoming[0].get("proposals") if incoming else []
    proposals = proposals if isinstance(proposals, list) else []
    data = node_data(node)
    groups: dict[str, dict[str, Any]] = {}
    for proposal in proposals:
        if not isinstance(proposal, dict):
            continue
        rows = _proposal_rows(proposal)
        key = _rows_key(rows)
        group = groups.setdefault(
            key,
            {
                "group_id": f"group_{len(groups) + 1}",
                "rows": rows,
                "proposal_node_ids": [],
                "count": 0,
            },
        )
        group["count"] += 1
        group["proposal_node_ids"].append(proposal.get("proposal_node_id"))

    ordered = sorted(groups.values(), key=lambda g: int(g.get("count") or 0), reverse=True)
    selected = ordered[0] if ordered and int(ordered[0].get("count") or 0) > len(proposals) / 2 else None
    reason = (
        f"{selected['count']} of {len(proposals)} proposals matched at document level."
        if selected
        else "No majority equivalent proposal group was found."
    )
    vlm_review = await _call_workflow_vlm(
        db,
        run,
        data,
        node_type="VLMJudge",
        input_payload={
            "processing_mode": run.processing_mode,
            "proposals": proposals,
            "equivalent_groups": ordered,
            "deterministic_selected_group": selected,
            "deterministic_reason": reason,
        },
        output_contract=(
            "Return JSON with keys: selected_group_id, equivalent_group_ids, reason, confidence. "
            "Do not rewrite OCR rows."
        ),
    )
    review_data = vlm_review.get("data") if isinstance(vlm_review.get("data"), dict) else {}
    judge_reason = review_data.get("reason") or reason
    _record_plugin_payload(
        db,
        run,
        node,
        {
            "proposals": proposals,
            "equivalent_groups": ordered,
            "selected_group": selected,
            "reason": reason,
            "provider": vlm_review.get("provider"),
            "model": vlm_review.get("model"),
            "skill_key": data.get("skillKey"),
            "vlm_review": vlm_review,
            "summary": _node_detail_summary(
                group_count=len(ordered),
                selected=bool(selected),
                reason=judge_reason,
            ),
        },
        status="completed",
    )


async def vote_selector_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    incoming = _incoming_payloads(db, run, str(node.get("id") or ""))
    report = incoming[0] if incoming else {}
    selected = report.get("selected_group") if isinstance(report.get("selected_group"), dict) else None
    proposals = _proposals_from_payloads(incoming)
    vote_reason = report.get("reason") or "Vote selector did not receive a judge reason."
    payload = {
        "selected_rows": selected.get("rows") if selected else [],
        "selected_group": selected,
        "proposals": proposals,
        "reason": vote_reason,
        "policy": node_data(node).get("policy") or "majority",
        "skill_key": node_data(node).get("skillKey"),
        "summary": _node_detail_summary(
            selected=bool(selected),
            row_count=len(selected.get("rows") if selected else []),
            reason=vote_reason,
        ),
    }
    _record_plugin_payload(db, run, node, payload, status="completed")


async def manager_review_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    node_id = str(node.get("id") or "manager")
    incoming = _incoming_payloads(db, run, node_id)
    selected = incoming[0] if incoming else {}
    rows = selected.get("selected_rows") if isinstance(selected.get("selected_rows"), list) else []
    proposals = _proposals_from_payloads(incoming)
    if not proposals:
        proposals = _pool_proposals_for_manager(db, run, node_id)
    tie_break = not rows and bool(proposals)
    if not rows and isinstance(selected.get("merged_ocr"), list):
        rows = selected["merged_ocr"]
    data = node_data(node)
    if tie_break:
        output_contract = (
            "Return JSON with keys: status, reason, revised_rows. "
            "revised_rows must be a list of OCR row objects reconciling all proposals. "
            "Within the same source file, do not return multiple rows with the same amount, vendor, and date. "
            "status must be pass or fail."
        )
        input_payload = {
            "processing_mode": run.processing_mode,
            "proposals": proposals,
            "vote_reason": selected.get("reason"),
            "tie_break": True,
        }
    else:
        output_contract = (
            "Return JSON with keys: status, reason, manager_feedback, revised_rows. "
            "status must be pass or fail. revised_rows may refine selected_rows. "
            "Within the same source file, do not return multiple rows with the same amount, vendor, and date."
        )
        input_payload = {
            "processing_mode": run.processing_mode,
            "selected_rows": rows,
            "selected_group": selected.get("selected_group"),
            "vote_reason": selected.get("reason"),
            "retry_on_fail": data.get("retryOnFail", True),
        }
    mode = (run.processing_mode or "").upper()
    run_files = _completed_run_files(db, run) if mode in ("AR", "AP") else []
    file_order = [rf.task_file_id for rf in run_files]
    if mode in ("AR", "AP") and rows:
        rows = clean_manager_ar_ap_rows(rows)
        if not tie_break:
            input_payload["selected_rows"] = rows
    llm_input_payload = _slim_manager_input_payload(input_payload, tie_break=tie_break)
    try:
        vlm_review = await _call_workflow_vlm(
            db,
            run,
            data,
            node_type="ManagerReview",
            input_payload=llm_input_payload,
            output_contract=output_contract,
        )
    except Exception as exc:
        message = str(exc)[:200]
        _set_node_state(
            run,
            node_id,
            "failed",
            {"error": message, "provider": data.get("provider") or default_workflow_provider()},
        )
        _set_workflow_error(run, f"Manager Review failed: {message}")
        db.commit()
        raise
    review_data = vlm_review.get("data") if isinstance(vlm_review.get("data"), dict) else {}
    revised = review_data.get("revised_rows")
    if isinstance(revised, list) and revised:
        rows = revised
    if mode in ("AR", "AP"):
        row_ids = row_file_ids_for_rows(rows, run_files) if run_files else None
        rows = clean_manager_ar_ap_rows(
            rows,
            file_order=file_order or None,
            row_file_ids=row_ids,
        )
    passed = bool(rows)
    mgr_reason = (
        review_data.get("reason")
        or selected.get("reason")
        or ("Manager tie-break produced revised rows." if tie_break else "Manager review completed.")
    )
    mgr_feedback = review_data.get("manager_feedback") or (
        None if passed else "Manager review did not produce revised rows."
    )
    payload = {
        "status": "pass" if passed else "fail",
        "selected_rows": rows,
        "revised_rows": rows,
        "manager_feedback": mgr_feedback,
        "reason": mgr_reason,
        "provider": vlm_review.get("provider"),
        "model": vlm_review.get("model"),
        "skill_key": data.get("skillKey"),
        "vlm_review": vlm_review,
        "summary": _node_detail_summary(
            status="pass" if passed else "fail",
            row_count=len(rows),
            reason=mgr_reason,
            manager_feedback=mgr_feedback,
        ),
    }
    _record_plugin_payload(db, run, node, payload, status="completed" if passed else "failed")


async def condition_router_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    incoming = _incoming_payloads(db, run, str(node.get("id") or ""))
    payload = incoming[0] if incoming else {"status": "fail", "reason": "No incoming verification report."}
    payload = {**payload, "summary": {"route": payload.get("status") or "unknown"}}
    _record_plugin_payload(db, run, node, payload, status="completed")


async def external_api_call_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    data = node_data(node)
    if not data.get("dangerAcknowledged"):
        raise ValueError("External API node requires dangerAcknowledged before execution")
    incoming = _incoming_payloads(db, run, str(node.get("id") or ""))
    _record_plugin_payload(
        db,
        run,
        node,
        {
            "status": "skipped",
            "reason": "External API execution is configured but not implemented in this safe plugin slice.",
            "input_count": len(incoming),
            "summary": {"status": "skipped"},
        },
        status="skipped",
    )


async def merge_result_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    node_id = str(node.get("id") or "merge")
    incoming = _incoming_payloads(db, run, str(node.get("id") or ""))
    report = incoming[0] if incoming else {}
    rows = report.get("revised_rows") if isinstance(report.get("revised_rows"), list) else []
    if not rows:
        rows = report.get("selected_rows") if isinstance(report.get("selected_rows"), list) else []
    run_files = _completed_run_files(db, run)
    if not rows:
        _set_workflow_error(run, "Merge Result received no rows from manager or vote path.")
        _set_node_state(run, node_id, "failed", {"row_count": 0})
        db.commit()
        raise ValueError("Merge Result is vital but received no OCR rows.")
    if (run.processing_mode or "").upper() in ("AR", "AP"):
        row_ids = row_file_ids_for_rows(rows, run_files)
        rows = clean_manager_ar_ap_rows(
            rows,
            file_order=[rf.task_file_id for rf in run_files],
            row_file_ids=row_ids,
        )
    states = _workflow_states(run)
    states["merged_ocr"] = rows
    states["table_source"] = "merge"
    run.node_states_json = states
    _apply_vlm_ocr_states(run, run_files, rows)
    ok_count = sum(1 for rf in run_files if rf.file_status == "ok")
    warn_count = sum(1 for rf in run_files if rf.file_status == "warning")
    _finalize_run_after_vlm(
        run,
        run_files,
        ok_count=ok_count,
        warn_count=warn_count,
        console_message=f"Merge finished. {len(rows)} row(s) in table.",
        vlm_node_id=node_id,
        merged_rows=rows,
    )
    store_executed_graph_hash(run)
    merge_reason = report.get("reason") or report.get("manager_feedback")
    _record_plugin_payload(
        db,
        run,
        node,
        {
            "merged_ocr": rows,
            "reason": merge_reason,
            "summary": _node_detail_summary(row_count=len(rows), reason=merge_reason),
        },
    )


async def table_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    node_id = str(node.get("id") or "table")
    _set_node_state(run, node_id, "active", None)
    db.commit()


async def coa_node(db: Session, run: WorkflowRun, _node: dict[str, Any], _emit: EmitFn | None) -> None:
    states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
    payload = states.get("approved_payload")
    if not isinstance(payload, dict):
        raise ValueError("Missing approved_payload for CoA deploy")
    skip = bool(states.get("skip_coa"))
    await WorkflowService.deploy_approved_coa(db, run, payload, skip_coa=skip)


async def save_node(db: Session, run: WorkflowRun, node: dict[str, Any], _emit: EmitFn | None) -> None:
    states = run.node_states_json if isinstance(run.node_states_json, dict) else {}
    payload = states.get("approved_payload")
    if not isinstance(payload, dict):
        raise ValueError("Missing approved_payload for save")
    await WorkflowService.save_approved_snapshot(db, run, payload, node=node)


NODE_HANDLERS: dict[str, NodeHandler] = {
    "Files": files_node,
    "ModeConfig": mode_node,
    "ReceiptStyle": receipt_node,
    "VLM_API": vlm_node,
    "If": if_node,
    "Switch": switch_node,
    "VLMDoubleCheck": vlm_double_check_node,
    "VLMProposer": vlm_proposer_node,
    "ProposalPoolJoin": proposal_pool_join_node,
    "VLMJudge": vlm_judge_node,
    "VoteSelector": vote_selector_node,
    "ManagerReview": manager_review_node,
    "ConditionRouter": condition_router_node,
    "ExternalApiCall": external_api_call_node,
    "MergeResult": merge_result_node,
    "TableReview": table_node,
    "CoADeploy": coa_node,
    "SaveResult": save_node,
}

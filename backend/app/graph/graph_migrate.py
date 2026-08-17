"""Migrate saved workflow graphs to schema V2."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.graph.graph_schema_v2 import ensure_graph_v2, graph_version, GRAPH_VERSION
from app.models.workflow import WorkflowRun, WorkflowTemplate


def migrate_graph_json(graph_json: dict, processing_mode: str) -> dict:
    return ensure_graph_v2(graph_json, processing_mode)


def migrate_all_saved_graphs(db: Session, *, company_id: str | None = None) -> int:
    """Normalize runs and templates to graph schema V2. Returns count migrated."""
    count = 0
    run_q = db.query(WorkflowRun)
    tpl_q = db.query(WorkflowTemplate)
    if company_id:
        run_q = run_q.filter(WorkflowRun.company_id == company_id)
        tpl_q = tpl_q.filter(WorkflowTemplate.company_id == company_id)

    for run in run_q.all():
        if graph_version(run.graph_json) >= GRAPH_VERSION:
            continue
        run.graph_json = ensure_graph_v2(run.graph_json, run.processing_mode)
        count += 1

    for tpl in tpl_q.all():
        if graph_version(tpl.graph_json) >= GRAPH_VERSION:
            continue
        tpl.graph_json = ensure_graph_v2(tpl.graph_json, tpl.processing_mode)
        count += 1

    if count:
        db.commit()
    return count

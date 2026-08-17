from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.workflow import WorkflowSkill, WorkflowSkillVersion

VALID_WORKFLOW_SKILL_MODES = {"AR", "AP", "BANK", "OTHER", "RECON"}
DEFAULT_SKILL_KEYS = (
    "vlm_judge_equivalence",
    "majority_vote",
    "manager_review",
    "retry_policy",
)
STRUCTURED_FIELDS = (
    "role",
    "rules",
    "input_context",
    "output_format",
    "failure_handling",
    "retry_policy",
    "selection_reason",
)


def default_manager_review_skill(mode: str) -> dict[str, str]:
    m = (mode or "AP").strip().upper()
    return {
        "role": (
            f"Act as the Manager Review agent for {m}. You are the final gate before the review table: "
            "auto-clean duplicate and junk rows, merge or correct fields from multiple VLM passes, "
            "and return only manager-approved rows. Flag uncertain cases; do not block the run."
        ),
        "rules": (
            "Per source file: auto-remove duplicate rows when amount, vendor, and date match (keep first in OCR order). "
            "Auto-remove rows with empty amount when another row on the same file has a non-empty amount. "
            "Keep but flag rows when the same file has different amounts (true multi-receipt page). "
            "Auto-remove OCR noise such as analysis summaries and non-transaction fragments. "
            "Keep but flag low-confidence rows. "
            "At run level, auto-remove cross-file duplicates when vendor, amount, and date match; "
            "keep the row from the first file in batch order. "
            "When multiple proposals exist, reconcile payee, date, amount, currency, and invoice number from evidence. "
            "Never invent amounts or vendors. Preserve source_file on every row."
        ),
        "input_context": (
            "Use selected_rows or proposals, processing_mode, vote reason, and per-row file metadata. "
            "Apply rules per file first, then run-level dedup. Deterministic cleanup may run before and after you."
        ),
        "output_format": (
            "Return strict JSON with keys status, reason, manager_feedback, revised_rows. "
            "revised_rows must contain only rows that should appear in the table. "
            "Flagged rows include needs_review true and optional review_reason."
        ),
        "failure_handling": (
            "If evidence conflicts, keep the best-supported row and flag it. "
            "Return status pass with warnings unless zero valid transaction rows remain, then status fail with clear feedback."
        ),
        "retry_policy": (
            "Retry only when retry_on_fail is true and manager feedback can improve extraction. "
            "Dedup and cleanup should not require retry."
        ),
        "selection_reason": (
            "One concise sentence explaining what was kept, removed, or flagged "
            "(for example removed duplicate rows on the same page)."
        ),
    }


def default_structured_skill(mode: str, skill_key: str) -> dict[str, str]:
    if (skill_key or "").strip().lower() == "manager_review":
        return default_manager_review_skill(mode)
    title = skill_key.replace("_", " ").title()
    return {
        "role": f"Act as the {title} workflow skill for {mode}.",
        "rules": "Follow the workflow node configuration and preserve accounting evidence.",
        "input_context": "Use only the current workflow inputs, prior node outputs, and attached document evidence.",
        "output_format": "Return structured JSON matching the node output schema.",
        "failure_handling": "If evidence conflicts, return a clear failure reason and do not invent missing values.",
        "retry_policy": "Retry only when the workflow node allows retry and feedback can improve the result.",
        "selection_reason": "Explain why the selected result is preferred in one concise reason.",
    }


def normalize_structured_skill(raw: dict[str, Any] | None, mode: str, skill_key: str) -> dict[str, str]:
    defaults = default_structured_skill(mode, skill_key)
    if not isinstance(raw, dict):
        return defaults
    out: dict[str, str] = {}
    for field in STRUCTURED_FIELDS:
        value = raw.get(field, defaults[field])
        out[field] = str(value or defaults[field]).strip()
    return out


def render_skill_markdown(mode: str, skill_key: str, structured: dict[str, Any]) -> str:
    normalized = normalize_structured_skill(structured, mode, skill_key)
    title = skill_key.replace("_", " ").title()
    return "\n\n".join(
        [
            f"# {title} Skill",
            f"Mode: {mode}",
            f"Skill Key: {skill_key}",
            "## Role\n" + normalized["role"],
            "## Rules\n" + normalized["rules"],
            "## Input Context\n" + normalized["input_context"],
            "## Output Format\n" + normalized["output_format"],
            "## Failure Handling\n" + normalized["failure_handling"],
            "## Retry Policy\n" + normalized["retry_policy"],
            "## Selection Reason\n" + normalized["selection_reason"],
        ]
    )


def _mode(mode: str) -> str:
    value = (mode or "AP").strip().upper()
    if value not in VALID_WORKFLOW_SKILL_MODES:
        raise ValueError("Invalid workflow skill mode")
    return value


def _skill_key(skill_key: str) -> str:
    value = (skill_key or "").strip().lower()
    if not value:
        raise ValueError("skill_key is required")
    return value


def get_or_create_skill(db: Session, company_id: str, mode: str, skill_key: str) -> WorkflowSkill:
    m = _mode(mode)
    key = _skill_key(skill_key)
    row = (
        db.query(WorkflowSkill)
        .filter(
            WorkflowSkill.company_id == company_id,
            WorkflowSkill.mode == m,
            WorkflowSkill.skill_key == key,
        )
        .first()
    )
    if row:
        return row
    structured = default_structured_skill(m, key)
    row = WorkflowSkill(
        company_id=company_id,
        mode=m,
        skill_key=key,
        structured_json=structured,
        generated_markdown=render_skill_markdown(m, key, structured),
        version=1,
    )
    db.add(row)
    db.flush()
    return row


def list_skills(db: Session, company_id: str, mode: str | None = None) -> list[WorkflowSkill]:
    modes = [_mode(mode)] if mode else ["AR", "AP", "BANK", "OTHER", "RECON"]
    for m in modes:
        for key in DEFAULT_SKILL_KEYS:
            get_or_create_skill(db, company_id, m, key)
    db.commit()
    q = db.query(WorkflowSkill).filter(WorkflowSkill.company_id == company_id)
    if mode:
        q = q.filter(WorkflowSkill.mode == _mode(mode))
    return q.order_by(WorkflowSkill.mode.asc(), WorkflowSkill.skill_key.asc()).all()


def update_skill(
    db: Session,
    row: WorkflowSkill,
    structured: dict[str, Any],
    *,
    user_id: str | None,
) -> WorkflowSkill:
    db.add(
        WorkflowSkillVersion(
            skill_id=row.id,
            company_id=row.company_id,
            mode=row.mode,
            skill_key=row.skill_key,
            version=row.version,
            structured_json=row.structured_json,
            generated_markdown=row.generated_markdown,
            saved_by_user_id=user_id,
        )
    )
    normalized = normalize_structured_skill(structured, row.mode, row.skill_key)
    row.structured_json = normalized
    row.generated_markdown = render_skill_markdown(row.mode, row.skill_key, normalized)
    row.version = int(row.version or 1) + 1
    row.updated_by_user_id = user_id
    db.flush()

    old_versions = (
        db.query(WorkflowSkillVersion)
        .filter(WorkflowSkillVersion.skill_id == row.id)
        .order_by(WorkflowSkillVersion.version.desc())
        .offset(2)
        .all()
    )
    for old in old_versions:
        db.delete(old)
    db.commit()
    db.refresh(row)
    return row


def reset_skill(db: Session, row: WorkflowSkill, *, user_id: str | None) -> WorkflowSkill:
    return update_skill(
        db,
        row,
        default_structured_skill(row.mode, row.skill_key),
        user_id=user_id,
    )


def rollback_skill(db: Session, row: WorkflowSkill, version: int | None, *, user_id: str | None) -> WorkflowSkill:
    q = db.query(WorkflowSkillVersion).filter(WorkflowSkillVersion.skill_id == row.id)
    if version is not None:
        q = q.filter(WorkflowSkillVersion.version == version)
    previous = q.order_by(WorkflowSkillVersion.version.desc()).first()
    if not previous:
        raise ValueError("No workflow skill version available for rollback")
    return update_skill(db, row, previous.structured_json, user_id=user_id)


def skill_out(row: WorkflowSkill, db: Session | None = None) -> dict[str, Any]:
    versions: list[dict[str, Any]] = []
    if db is not None:
        versions = [
            {"version": v.version, "saved_at": v.saved_at.isoformat() if v.saved_at else None}
            for v in (
                db.query(WorkflowSkillVersion)
                .filter(WorkflowSkillVersion.skill_id == row.id)
                .order_by(WorkflowSkillVersion.version.desc())
                .limit(2)
                .all()
            )
        ]
    return {
        "id": row.id,
        "company_id": row.company_id,
        "mode": row.mode,
        "skill_key": row.skill_key,
        "structured_json": row.structured_json,
        "generated_markdown": row.generated_markdown,
        "version": row.version,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "previous_versions": versions,
    }

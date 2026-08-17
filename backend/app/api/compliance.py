import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user_id
from app.database import get_db
from app.models.compliance import AuditPackageArchive, OcrCompletionEvent
from app.models.reconciliation import ReconciliationAudit, ReconciliationMatch
from app.models.rule_events import CompanyRuleAuditLog, CompanyRuleHitEvent

router = APIRouter()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _build_audit_package_payload(
    *,
    db: Session,
    company_id: str,
    trace_id: Optional[str],
    since_days: int,
    limit: int,
) -> dict[str, Any]:
    capped_days = max(1, min(since_days, 365))
    capped_limit = max(1, min(limit, 2000))
    cutoff = datetime.utcnow() - timedelta(days=capped_days)

    audit_query = db.query(CompanyRuleAuditLog).filter(
        CompanyRuleAuditLog.company_id == company_id,
        CompanyRuleAuditLog.created_at >= cutoff,
    )
    hit_query = db.query(CompanyRuleHitEvent).filter(
        CompanyRuleHitEvent.company_id == company_id,
        CompanyRuleHitEvent.created_at >= cutoff,
    )
    ocr_event_query = db.query(OcrCompletionEvent).filter(
        OcrCompletionEvent.company_id == company_id,
        OcrCompletionEvent.created_at >= cutoff,
    )
    recon_audit_query = db.query(ReconciliationAudit).filter(
        ReconciliationAudit.company_id == company_id,
        ReconciliationAudit.timestamp >= cutoff,
    )
    recon_match_query = db.query(ReconciliationMatch).filter(
        ReconciliationMatch.company_id == company_id,
        ReconciliationMatch.created_at >= cutoff,
    )
    if trace_id:
        audit_query = audit_query.filter(CompanyRuleAuditLog.trace_id == trace_id)
        hit_query = hit_query.filter(CompanyRuleHitEvent.trace_id == trace_id)
        ocr_event_query = ocr_event_query.filter(OcrCompletionEvent.trace_id == trace_id)
        recon_audit_query = recon_audit_query.filter(ReconciliationAudit.trace_id == trace_id)
        recon_match_query = recon_match_query.filter(ReconciliationMatch.trace_id == trace_id)

    audit_rows = audit_query.order_by(CompanyRuleAuditLog.created_at.desc()).limit(capped_limit).all()
    hit_rows = hit_query.order_by(CompanyRuleHitEvent.created_at.desc()).limit(capped_limit).all()
    ocr_event_rows = ocr_event_query.order_by(OcrCompletionEvent.created_at.desc()).limit(capped_limit).all()
    recon_audit_rows = recon_audit_query.order_by(ReconciliationAudit.timestamp.desc()).limit(capped_limit).all()
    recon_match_rows = recon_match_query.order_by(ReconciliationMatch.created_at.desc()).limit(capped_limit).all()

    trace_ids = {
        *(row.trace_id for row in audit_rows if row.trace_id),
        *(row.trace_id for row in hit_rows if row.trace_id),
        *(row.trace_id for row in ocr_event_rows if row.trace_id),
        *(row.trace_id for row in recon_audit_rows if row.trace_id),
        *(row.trace_id for row in recon_match_rows if row.trace_id),
    }

    # Link reconciliation match rows back to match audit evidence.
    evidence_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for row in recon_audit_rows:
        payload = row.payload_json or {}
        bank_id = payload.get("bank_txn_id")
        ledger_id = payload.get("ledger_txn_id")
        if row.action == "unmatch" and isinstance(payload.get("original_match"), dict):
            original = payload.get("original_match") or {}
            bank_id = bank_id or original.get("bank_txn_id")
            ledger_id = ledger_id or original.get("ledger_txn_id")
        if bank_id and ledger_id:
            evidence_by_pair[(str(bank_id), str(ledger_id))] = payload.get("decision_evidence") or {}

    payload: dict[str, Any] = {
        "company_id": company_id,
        "trace_id_filter": trace_id,
        "generated_at": datetime.utcnow().isoformat(),
        "window_days": capped_days,
        "immutable_note": (
            "Immutable compliance package. Verify integrity with package_hash before external sharing."
        ),
        "summary": {
            "trace_count": len(trace_ids),
            "ocr_event_count": len(ocr_event_rows),
            "rule_audit_count": len(audit_rows),
            "rule_hit_count": len(hit_rows),
            "reconciliation_audit_count": len(recon_audit_rows),
            "reconciliation_match_count": len(recon_match_rows),
        },
        "ocr_events": [
            {
                "id": row.id,
                "trace_id": row.trace_id,
                "filename": row.filename,
                "stage": row.stage,
                "source": row.source,
                "created_at": _iso(row.created_at),
                "decision_evidence": (row.metadata_json or {}).get("decision_evidence") or {},
            }
            for row in ocr_event_rows
        ],
        "rule_audit": [
            {
                "id": row.id,
                "trace_id": row.trace_id,
                "rule_id": row.rule_id,
                "action": row.action,
                "actor_user_id": row.actor_user_id,
                "created_at": _iso(row.created_at),
                "decision_evidence": (row.metadata_json or {}).get("decision_evidence") or {},
            }
            for row in audit_rows
        ],
        "rule_hits": [
            {
                "id": row.id,
                "trace_id": row.trace_id,
                "rule_id": row.rule_id,
                "source": row.source,
                "created_at": _iso(row.created_at),
                "decision_evidence": (row.metadata_json or {}).get("decision_evidence") or {},
            }
            for row in hit_rows
        ],
        "reconciliation_audit": [
            {
                "id": row.id,
                "trace_id": row.trace_id,
                "action": row.action,
                "user_id": row.user_id,
                "timestamp": _iso(row.timestamp),
                "decision_evidence": (row.payload_json or {}).get("decision_evidence") or {},
            }
            for row in recon_audit_rows
        ],
        "reconciliation_matches": [
            {
                "id": row.id,
                "trace_id": row.trace_id,
                "bank_txn_id": row.bank_txn_id,
                "ledger_txn_id": row.ledger_txn_id,
                "match_type": row.match_type.value if hasattr(row.match_type, "value") else row.match_type,
                "score": float(row.score),
                "decision": row.decision.value if hasattr(row.decision, "value") else row.decision,
                "created_by": row.created_by,
                "created_at": _iso(row.created_at),
                "decision_evidence": evidence_by_pair.get((str(row.bank_txn_id), str(row.ledger_txn_id)), {}),
            }
            for row in recon_match_rows
        ],
    }

    payload["package_hash"] = _hash_payload(payload)
    return payload


class AuditPackageArchiveRequest(BaseModel):
    trace_id: Optional[str] = None
    since_days: int = 30
    limit: int = 200
    note: Optional[str] = None


@router.get("/compliance/trace/{trace_id}")
async def get_trace_timeline(
    trace_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    audit_rows = db.query(CompanyRuleAuditLog).filter(
        CompanyRuleAuditLog.company_id == company_id,
        CompanyRuleAuditLog.trace_id == trace_id,
    ).order_by(CompanyRuleAuditLog.created_at.asc()).all()
    hit_rows = db.query(CompanyRuleHitEvent).filter(
        CompanyRuleHitEvent.company_id == company_id,
        CompanyRuleHitEvent.trace_id == trace_id,
    ).order_by(CompanyRuleHitEvent.created_at.asc()).all()
    ocr_event_rows = db.query(OcrCompletionEvent).filter(
        OcrCompletionEvent.company_id == company_id,
        OcrCompletionEvent.trace_id == trace_id,
    ).order_by(OcrCompletionEvent.created_at.asc()).all()
    recon_audit_rows = db.query(ReconciliationAudit).filter(
        ReconciliationAudit.company_id == company_id,
        ReconciliationAudit.trace_id == trace_id,
    ).order_by(ReconciliationAudit.timestamp.asc()).all()

    timeline = []
    for row in ocr_event_rows:
        metadata = row.metadata_json or {}
        timeline.append(
            {
                "type": row.stage,
                "at": _iso(row.created_at),
                "trace_id": row.trace_id,
                "id": row.id,
                "filename": row.filename,
                "source": row.source,
                "decision_evidence": metadata.get("decision_evidence") or {},
            }
        )
    for row in audit_rows:
        metadata = row.metadata_json or {}
        timeline.append(
            {
                "type": "rule_audit",
                "at": _iso(row.created_at),
                "trace_id": row.trace_id,
                "id": row.id,
                "rule_id": row.rule_id,
                "action": row.action,
                "actor_user_id": row.actor_user_id,
                "decision_evidence": metadata.get("decision_evidence") or {},
            }
        )
    for row in hit_rows:
        metadata = row.metadata_json or {}
        timeline.append(
            {
                "type": "rule_hit",
                "at": _iso(row.created_at),
                "trace_id": row.trace_id,
                "id": row.id,
                "rule_id": row.rule_id,
                "source": row.source,
                "decision_evidence": metadata.get("decision_evidence") or {},
            }
        )
    for row in recon_audit_rows:
        payload = row.payload_json or {}
        timeline.append(
            {
                "type": "reconciliation_audit",
                "at": _iso(row.timestamp),
                "trace_id": row.trace_id,
                "id": row.id,
                "action": row.action,
                "user_id": row.user_id,
                "decision_evidence": payload.get("decision_evidence") or {},
            }
        )

    timeline.sort(key=lambda item: item.get("at") or "")
    return {
        "company_id": company_id,
        "trace_id": trace_id,
        "event_count": len(timeline),
        "timeline": timeline,
    }


@router.get("/compliance/audit-package")
async def get_audit_package(
    trace_id: Optional[str] = None,
    since_days: int = 30,
    limit: int = 200,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    return _build_audit_package_payload(
        db=db,
        company_id=company_id,
        trace_id=trace_id,
        since_days=since_days,
        limit=limit,
    )


@router.post("/compliance/audit-package/archive")
async def archive_audit_package(
    payload: AuditPackageArchiveRequest,
    company_id: str = Depends(get_current_company_id),
    user_id: str = Depends(get_current_user_id),
    db: Session = Depends(get_db),
):
    package = _build_audit_package_payload(
        db=db,
        company_id=company_id,
        trace_id=payload.trace_id,
        since_days=payload.since_days,
        limit=payload.limit,
    )
    archive = AuditPackageArchive(
        company_id=company_id,
        trace_id_filter=payload.trace_id,
        content_hash=str(package.get("package_hash") or ""),
        payload_json=package,
        immutable_note=payload.note
        or "Archived immutable compliance package snapshot",
        created_by=user_id,
    )
    db.add(archive)
    db.commit()
    return {
        "status": "ok",
        "archive_id": archive.id,
        "content_hash": archive.content_hash,
        "created_at": _iso(archive.created_at),
    }


@router.get("/compliance/audit-package/archive/{archive_id}")
async def get_archived_audit_package(
    archive_id: str,
    company_id: str = Depends(get_current_company_id),
    db: Session = Depends(get_db),
):
    row = db.query(AuditPackageArchive).filter(
        AuditPackageArchive.id == archive_id,
        AuditPackageArchive.company_id == company_id,
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="archive not found")
    payload = row.payload_json or {}
    expected_hash = row.content_hash or ""
    actual_hash = _hash_payload(payload)
    return {
        "archive_id": row.id,
        "company_id": row.company_id,
        "trace_id_filter": row.trace_id_filter,
        "created_by": row.created_by,
        "created_at": _iso(row.created_at),
        "content_hash": expected_hash,
        "integrity_ok": expected_hash == actual_hash,
        "payload": payload,
    }

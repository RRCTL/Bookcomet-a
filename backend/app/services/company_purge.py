"""
Hard-delete all data for a company (workspace), then the company row.
Used only from DELETE /companies/{id} after owner + confirm-name checks.
"""
from __future__ import annotations

import logging
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _unlink_task_files_for_company(db: Session, company_id: str) -> None:
    from sqlalchemy import and_
    from app.models.chat import ChatTask, TaskFile

    for row in (
        db.query(TaskFile)
        .join(ChatTask, TaskFile.task_id == ChatTask.id)
        .filter(and_(ChatTask.company_id == company_id))
        .all()
    ):
        p = row.storage_path
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("[CompanyPurge] unlink failed: %s (%s)", p, exc)


def delete_company_purge(db: Session, company_id: str) -> None:
    """Delete a company and all related rows. Caller commits."""
    if not (company_id or "").strip():
        raise ValueError("company_id required")

    cid = company_id.strip()

    from app.models.other import (
        AssetDepreciationSchedule,
        OtherRecord,
        FixedAsset,
        LoanInstallment,
        LoanRecord,
    )
    from app.models.background_job import BackgroundJob
    from app.models.chat import ChatTask, TaskAuditLog, TaskFile, TaskMessage, TaskStateSnapshot
    from app.models.company_context import CompanyProfile, CompanyRule
    from app.models.company_manual import CompanyManual, CompanyManualVersion
    from app.models.compliance import AuditPackageArchive, OcrCompletionEvent
    from app.models.exclusion_rule import ExclusionRule
    from app.models.gl_journal import GlJournal, GlJournalLine
    from app.models.identity import Company, Membership
    from app.models.memory import SessionSummary, TokenUsageLog
    from app.models.ocr_journal import OcrJournal, OcrJournalLine
    from app.models.reconciliation import (
        ChartOfAccountEntry,
        ReconSession,
        ReconciliationAudit,
        ReconciliationGroup,
        ReconciliationMatch,
    )
    from app.models.rule_events import CompanyRuleAuditLog, CompanyRuleHitEvent
    from app.models.rule_memory import CompanyRuleMemory, CompanyRuleMemoryVersion
    from app.models.transaction import BankTransaction, LedgerTransaction

    db.query(TokenUsageLog).filter(TokenUsageLog.company_id == cid).delete(
        synchronize_session=False
    )

    db.query(OcrCompletionEvent).filter(OcrCompletionEvent.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(AuditPackageArchive).filter(AuditPackageArchive.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(CompanyRuleHitEvent).filter(CompanyRuleHitEvent.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(CompanyRuleAuditLog).filter(CompanyRuleAuditLog.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(ReconSession).filter(ReconSession.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(ReconciliationAudit).filter(ReconciliationAudit.company_id == cid).delete(
        synchronize_session=False
    )

    db.query(ReconciliationMatch).filter(ReconciliationMatch.company_id == cid).delete(
        synchronize_session=False
    )

    gj_ids = [r[0] for r in db.query(GlJournal.id).filter(GlJournal.company_id == cid).all()]
    if gj_ids:
        db.query(GlJournalLine).filter(GlJournalLine.journal_id.in_(gj_ids)).delete(
            synchronize_session=False
        )
    db.query(GlJournal).filter(GlJournal.company_id == cid).update(
        {GlJournal.reversal_of_journal_id: None},
        synchronize_session=False,
    )
    db.query(GlJournal).filter(GlJournal.company_id == cid).delete(synchronize_session=False)

    db.query(LoanInstallment).filter(LoanInstallment.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(AssetDepreciationSchedule).filter(AssetDepreciationSchedule.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(FixedAsset).filter(FixedAsset.company_id == cid).delete(synchronize_session=False)
    db.query(LoanRecord).filter(LoanRecord.company_id == cid).delete(synchronize_session=False)
    db.query(OtherRecord).filter(OtherRecord.company_id == cid).delete(
        synchronize_session=False
    )

    oj_ids = [r[0] for r in db.query(OcrJournal.id).filter(OcrJournal.company_id == cid).all()]
    if oj_ids:
        db.query(OcrJournalLine).filter(OcrJournalLine.journal_id.in_(oj_ids)).delete(
            synchronize_session=False
        )
    db.query(OcrJournal).filter(OcrJournal.company_id == cid).delete(synchronize_session=False)

    db.query(ReconciliationGroup).filter(ReconciliationGroup.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(BankTransaction).filter(BankTransaction.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(LedgerTransaction).filter(LedgerTransaction.company_id == cid).delete(
        synchronize_session=False
    )
    db.query(ChartOfAccountEntry).filter(ChartOfAccountEntry.company_id == cid).delete(
        synchronize_session=False
    )

    task_ids = [r[0] for r in db.query(ChatTask.id).filter(ChatTask.company_id == cid).all()]

    if task_ids:
        db.query(SessionSummary).filter(
            sa.or_(
                SessionSummary.company_id == cid,
                SessionSummary.task_id.in_(task_ids),
            )
        ).delete(synchronize_session=False)
    else:
        db.query(SessionSummary).filter(SessionSummary.company_id == cid).delete(
            synchronize_session=False
        )

    db.query(BackgroundJob).filter(BackgroundJob.company_id == cid).delete(
        synchronize_session=False
    )

    _unlink_task_files_for_company(db, cid)

    if task_ids:
        for model in (TaskMessage, TaskStateSnapshot, TaskAuditLog, TaskFile):
            db.query(model).filter(model.task_id.in_(task_ids)).delete(synchronize_session=False)

    db.query(ChatTask).filter(ChatTask.company_id == cid).delete(synchronize_session=False)

    for model in (ExclusionRule, CompanyRule, CompanyProfile):
        db.query(model).filter(model.company_id == cid).delete(synchronize_session=False)

    mem_ids = [r[0] for r in db.query(CompanyRuleMemory.id).filter(CompanyRuleMemory.company_id == cid).all()]
    if mem_ids:
        db.query(CompanyRuleMemoryVersion).filter(CompanyRuleMemoryVersion.memory_id.in_(mem_ids)).delete(
            synchronize_session=False
        )
    db.query(CompanyRuleMemory).filter(CompanyRuleMemory.company_id == cid).delete(
        synchronize_session=False
    )
    man_ids = [r[0] for r in db.query(CompanyManual.id).filter(CompanyManual.company_id == cid).all()]
    if man_ids:
        db.query(CompanyManualVersion).filter(CompanyManualVersion.manual_id.in_(man_ids)).delete(
            synchronize_session=False
        )
    db.query(CompanyManual).filter(CompanyManual.company_id == cid).delete(
        synchronize_session=False
    )

    # Legacy billing tables may still exist in older local DBs.
    bind = db.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("company_subscriptions"):
        db.execute(
            sa.text("DELETE FROM company_subscriptions WHERE company_id = :cid"),
            {"cid": cid},
        )
    if insp.has_table("billing_webhook_events") and "company_id" in {
        c["name"] for c in insp.get_columns("billing_webhook_events")
    }:
        db.execute(
            sa.text("DELETE FROM billing_webhook_events WHERE company_id = :cid"),
            {"cid": cid},
        )

    db.query(Membership).filter(Membership.company_id == cid).delete(synchronize_session=False)

    co = db.query(Company).filter(Company.id == cid).first()
    if co:
        db.delete(co)

    db.flush()

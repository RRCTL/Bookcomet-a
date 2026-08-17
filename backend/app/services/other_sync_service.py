"""
Other sync service — auto-syncs other_records → formal tables.

Called after every edit to an OtherRecord.  Performs an upsert
into loan_records / loan_installments (for loans) or
fixed_assets / asset_depreciation_schedule (for fixed assets).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.other import (
    AssetDepreciationSchedule,
    OtherRecord,
    FixedAsset,
    LoanInstallment,
    LoanRecord,
)
from app.services.depreciation_engine import compute_schedule, compute_loan_schedule

logger = logging.getLogger(__name__)


def _parse_date(val: Any) -> datetime | None:
    if not val:
        return None
    if isinstance(val, datetime):
        return val
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(str(val)[:10], fmt[:10])
        except ValueError:
            continue
    return None


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


# ── Loan sync ─────────────────────────────────────────────────────────────────

def upsert_loan_record(record: OtherRecord, db: Session) -> LoanRecord:
    p = record.payload_json or {}

    existing = (
        db.query(LoanRecord)
        .filter(LoanRecord.other_record_id == record.id)
        .first()
    )
    if existing:
        loan = existing
    else:
        loan = LoanRecord(
            id=str(uuid.uuid4()),
            company_id=record.company_id,
            other_record_id=record.id,
        )
        db.add(loan)

    loan.loan_reference = p.get("loan_reference")
    loan.lender_name = p.get("lender_name") or p.get("lender") or "Unknown"
    loan.lender_account = p.get("lender_account")
    loan.principal_amount = _safe_float(p.get("principal_amount"))
    loan.currency = p.get("currency") or "HKD"
    loan.interest_rate_pct = _safe_float(p.get("interest_rate_pct")) or None
    loan.tenor_months = _safe_int(p.get("tenor_months")) or None
    loan.monthly_installment = _safe_float(p.get("monthly_installment")) or None
    loan.start_date = _parse_date(p.get("start_date"))
    loan.maturity_date = _parse_date(p.get("maturity_date"))
    loan.first_payment_date = _parse_date(p.get("first_payment_date"))
    loan.outstanding_principal = _safe_float(p.get("outstanding_principal")) or None
    loan.status = p.get("status") or "active"
    loan.memo = p.get("memo")
    loan.document_type = p.get("document_type")
    db.flush()
    return loan


def regenerate_loan_installments(
    record: OtherRecord, db: Session
) -> None:
    loan = (
        db.query(LoanRecord)
        .filter(LoanRecord.other_record_id == record.id)
        .first()
    )
    if not loan:
        return

    principal = loan.principal_amount or 0.0
    rate = loan.interest_rate_pct or 0.0
    tenor = loan.tenor_months or 0
    start = loan.first_payment_date or loan.start_date

    if principal <= 0 or tenor <= 0:
        return

    # Delete existing installments
    db.query(LoanInstallment).filter(LoanInstallment.loan_id == loan.id).delete()

    schedule = compute_loan_schedule(
        principal=principal,
        annual_interest_rate_pct=rate,
        tenor_months=tenor,
        start_date=start,
    )
    for entry in schedule:
        db.add(
            LoanInstallment(
                id=str(uuid.uuid4()),
                loan_id=loan.id,
                company_id=loan.company_id,
                installment_number=entry["installment_number"],
                due_date=entry["due_date"],
                principal_portion=entry["principal_portion"],
                interest_portion=entry["interest_portion"],
                total_payment=entry["total_payment"],
                outstanding_principal_after=entry["outstanding_principal_after"],
                status="pending",
            )
        )

    logger.info(
        "[AssetSync] Regenerated %d loan installments for loan_id=%s",
        len(schedule),
        loan.id,
    )


# ── Asset sync ────────────────────────────────────────────────────────────────

def upsert_fixed_asset(record: OtherRecord, db: Session) -> FixedAsset:
    p = record.payload_json or {}

    existing = (
        db.query(FixedAsset)
        .filter(FixedAsset.other_record_id == record.id)
        .first()
    )
    if existing:
        asset = existing
    else:
        asset = FixedAsset(
            id=str(uuid.uuid4()),
            company_id=record.company_id,
            other_record_id=record.id,
        )
        db.add(asset)

    asset.asset_reference = p.get("asset_reference")
    asset.asset_name = p.get("asset_name") or "Unknown Asset"
    asset.asset_type = p.get("asset_type") or "equipment"
    asset.description = p.get("description")
    asset.purchase_amount = _safe_float(p.get("purchase_amount"))
    asset.acquisition_date = _parse_date(p.get("acquisition_date"))
    asset.currency = p.get("currency") or "HKD"
    asset.vendor = p.get("vendor")
    asset.invoice_ref = p.get("invoice_ref")
    asset.useful_life_months = _safe_int(p.get("useful_life_months")) or 60
    asset.residual_value = _safe_float(p.get("residual_value"))
    asset.depreciation_method = p.get("depreciation_method") or "straight_line"
    asset.status = p.get("status") or "active"
    asset.disposal_date = _parse_date(p.get("disposal_date"))
    asset.disposal_amount = _safe_float(p.get("disposal_amount")) or None
    db.flush()
    return asset


def recompute_depreciation_schedule(
    record: OtherRecord, db: Session
) -> None:
    asset = (
        db.query(FixedAsset)
        .filter(FixedAsset.other_record_id == record.id)
        .first()
    )
    if not asset:
        return

    if asset.purchase_amount <= 0 or asset.useful_life_months <= 0:
        return

    # Delete existing schedule
    db.query(AssetDepreciationSchedule).filter(
        AssetDepreciationSchedule.asset_id == asset.id
    ).delete()

    schedule = compute_schedule(
        purchase_amount=asset.purchase_amount,
        residual_value=asset.residual_value,
        useful_life_months=asset.useful_life_months,
        method=asset.depreciation_method,
        acquisition_date=asset.acquisition_date,
    )

    for entry in schedule:
        db.add(
            AssetDepreciationSchedule(
                id=str(uuid.uuid4()),
                asset_id=asset.id,
                company_id=asset.company_id,
                period_start=entry["period_start"],
                period_end=entry["period_end"],
                period_type=entry.get("period_type", "monthly"),
                depreciation_amount=entry["depreciation_amount"],
                accumulated_at_period_end=entry["accumulated_at_period_end"],
                net_book_value_at_period_end=entry["net_book_value_at_period_end"],
            )
        )

    # Update cached totals on FixedAsset
    if schedule:
        last = schedule[-1]
        asset.accumulated_depreciation = last["accumulated_at_period_end"]
        asset.net_book_value = last["net_book_value_at_period_end"]

    logger.info(
        "[AssetSync] Recomputed %d depreciation periods for asset_id=%s",
        len(schedule),
        asset.id,
    )


# ── Main entry-point ──────────────────────────────────────────────────────────

def sync_record(record: OtherRecord, db: Session) -> None:
    """
    Auto-sync other_records → formal tables.
    Called on every edit.  Errors are logged, never raised.
    """
    try:
        if record.record_type == "loan":
            upsert_loan_record(record, db)
            regenerate_loan_installments(record, db)
        elif record.record_type == "fixed_asset":
            upsert_fixed_asset(record, db)
            recompute_depreciation_schedule(record, db)
        db.commit()
        logger.info(
            "[AssetSync] Synced record id=%s type=%s", record.id, record.record_type
        )
    except Exception as exc:
        logger.error("[AssetSync] Sync failed for record id=%s: %s", record.id, exc)
        try:
            db.rollback()
        except Exception:
            pass

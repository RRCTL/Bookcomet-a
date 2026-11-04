"""
Other module API (loans / fixed assets)

POST /api/other/route
  — Called when user clicks "Route to Other" on the gate card.
  — Creates a new OTHER ChatTask + OtherRecord.
  — Runs LLM extraction (loan or fixed_asset) on the OCR text.
  — Auto-syncs to formal tables.

GET  /api/other/records?task_id=…
PATCH /api/other/records/{record_id}
GET  /api/other/records/{record_id}/depreciation
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_current_company_id, get_current_user
from app.core.gateway_settings import openai_chat_completions_url
from app.core.config import settings
from app.database import get_db
from app.services.abuse_guard import (
    build_hardened_system_prompt,
    sanitise_ocr_text,
    scan_output,
)
from app.models.other import (
    AssetDepreciationSchedule,
    OtherRecord,
    FixedAsset,
    LoanRecord,
)
from app.models.chat import ChatTask, TaskMessage
from app.services.other_sync_service import sync_record
from app.services.token_logger import log_token_usage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/other")

_DEPLOY_API_KEY = os.getenv("LLM_API_KEY") or os.getenv("DEPLOY_API_KEY", "")
_DEPLOY_BASE_URL = (
    os.getenv("LLM_BASE_URL") or os.getenv("DEPLOY_BASE_URL") or os.getenv("VLM_BASE_URL") or ""
).rstrip("/")


# ── Extraction prompts ─────────────────────────────────────────────────────────

_LOAN_EXTRACTION_PROMPT = """You are a financial data extraction assistant for Hong Kong loan / mortgage documents.
Extract the following fields from the OCR text and return a single valid JSON object.
Use null for absent fields. Do NOT guess. Output ONLY the JSON object.

Fields:
{
  "loan_reference": "loan/mortgage reference number or null",
  "lender_name": "bank or lender name",
  "lender_account": "lender account or branch or null",
  "principal_amount": "original loan principal as a number (e.g. 500000)",
  "currency": "currency code (e.g. HKD, USD)",
  "interest_rate_pct": "annual interest rate as a number (e.g. 3.5)",
  "tenor_months": "loan tenor in months as an integer (e.g. 120 for 10 years)",
  "monthly_installment": "monthly payment amount as a number",
  "start_date": "loan start date in YYYY-MM-DD or null",
  "maturity_date": "maturity date in YYYY-MM-DD or null",
  "first_payment_date": "first payment date in YYYY-MM-DD or null",
  "outstanding_principal": "current outstanding balance as a number or null",
  "status": "active | repaid | default | closed",
  "document_type": "loan_schedule | mortgage | hire_purchase | other",
  "memo": "any other key notes from the document or null"
}
"""

_ASSET_EXTRACTION_PROMPT = """You are a financial data extraction assistant for Hong Kong fixed asset purchase documents.
Extract the following fields from the OCR text and return a single valid JSON object.
Use null for absent fields. Do NOT guess. Output ONLY the JSON object.

Fields:
{
  "asset_reference": "asset tag or registration number or null",
  "asset_name": "name of the asset (e.g. Toyota Corolla, MacBook Pro, Office Unit 5F)",
  "asset_type": "vehicle | property | equipment | furniture | other",
  "description": "brief description or null",
  "purchase_amount": "total purchase price as a number",
  "acquisition_date": "date of purchase or transfer in YYYY-MM-DD or null",
  "currency": "currency code (e.g. HKD)",
  "vendor": "seller / vendor name or null",
  "invoice_ref": "invoice or agreement reference number or null",
  "useful_life_months": "estimated useful life in months as an integer (e.g. 60)",
  "residual_value": "estimated residual/salvage value as a number (default 0)",
  "depreciation_method": "straight_line | declining_balance",
  "status": "active | disposed | fully_depreciated",
  "memo": "any other key notes or null"
}
"""


def _call_extraction_llm(ocr_text: str, system_prompt: str) -> tuple[dict, dict]:
    """
    Call the LLM to extract structured fields from OCR text.
    Returns (extracted_dict, raw_response).
    """
    # Sanitise OCR text to prevent indirect prompt injection
    clean_ocr = sanitise_ocr_text(ocr_text)

    hardened_prompt = build_hardened_system_prompt(system_prompt)
    payload = {
        "model": settings.deploy_model,
        "messages": [
            {"role": "system", "content": hardened_prompt},
            {"role": "user", "content": f"Document OCR text:\n\n{clean_ocr[:4000]}"},
        ],
        "max_tokens": 1000,
        "temperature": 0.1,
    }
    resp = requests.post(
        openai_chat_completions_url(_DEPLOY_BASE_URL),
        headers={
            "Authorization": f"Bearer {_DEPLOY_API_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=(15, 90),
        verify=True,
    )
    resp.raise_for_status()
    raw = resp.json()
    content = raw["choices"][0]["message"]["content"].strip()
    # Output scanner
    _safe, content = scan_output(content)
    if not _safe:
        raise ValueError("LLM response blocked by security scanner.")

    # Strip markdown fences
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Try to extract first JSON object
        m = re.search(r"\{.*\}", content, re.DOTALL)
        data = json.loads(m.group(0)) if m else {}

    return data, raw


# ── Request / Response models ─────────────────────────────────────────────────

class RouteRequest(BaseModel):
    source_task_id: str
    source_file_id: str | None = None
    document_subtype: str = "loan"          # loan | fixed_asset
    ocr_text: str
    gate_document_hint: str | None = None   # REFERENCE_FINANCIAL | AMBIGUOUS


class RouteResponse(BaseModel):
    task_id: str
    record_id: str
    task_title: str


class RecordUpdateRequest(BaseModel):
    payload_json: dict[str, Any]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/route", response_model=RouteResponse)
async def route_to_other(
    req: RouteRequest,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
    current_user: Any = Depends(get_current_user),
) -> RouteResponse:
    """
    Route an OCR document to a new OTHER task.
    1. Extract structured fields via LLM.
    2. Create a new ChatTask with processing_mode="OTHER".
    3. Create an OtherRecord with the extracted payload.
    4. Auto-sync to formal tables.
    5. Add an initial assistant message summarising what was extracted.
    """
    now = datetime.now(timezone.utc)
    user_id = getattr(current_user, "id", None) or "system"

    # 1. LLM extraction
    prompt = (
        _LOAN_EXTRACTION_PROMPT
        if req.document_subtype == "loan"
        else _ASSET_EXTRACTION_PROMPT
    )
    try:
        extracted, llm_resp = _call_extraction_llm(req.ocr_text, prompt)
        log_token_usage(
            db,
            company_id,
            "asset_extract",
            settings.deploy_model,
            llm_resp,
            task_id=None,
        )
    except Exception as exc:
        logger.warning("[Other] LLM extraction failed: %s — using empty payload", exc)
        extracted = {}

    # Ensure record_type is set
    extracted["record_type"] = req.document_subtype

    # 2. Build a readable title
    if req.document_subtype == "loan":
        lender = extracted.get("lender_name") or "Loan"
        principal = extracted.get("principal_amount")
        title = f"Loan — {lender}" + (f" ({principal})" if principal else "")
    else:
        name = extracted.get("asset_name") or "Asset"
        amt = extracted.get("purchase_amount")
        title = f"Asset — {name}" + (f" ({amt})" if amt else "")

    # 3. Create new ChatTask
    task = ChatTask(
        id=str(uuid.uuid4()),
        company_id=company_id,
        owner_user_id=user_id,
        processing_mode="OTHER",
        title=title[:100],
        status="active",
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.flush()

    # 4. Create OtherRecord
    record = OtherRecord(
        id=str(uuid.uuid4()),
        company_id=company_id,
        task_id=task.id,
        record_type=req.document_subtype,
        payload_json=extracted,
        source_task_id=req.source_task_id,
        source_file_id=req.source_file_id,
    )
    db.add(record)
    db.flush()

    # 5. Auto-sync to formal tables
    sync_record(record, db)

    # 6. Add welcome system message to the new task
    intro_lines = [
        f"✅ Document routed to Other.",
        f"Type: **{req.document_subtype.replace('_', ' ').title()}**",
        "",
        "**Extracted fields:**",
    ]
    for k, v in extracted.items():
        if k != "record_type" and v is not None:
            intro_lines.append(f"- {k.replace('_', ' ').title()}: {v}")
    intro_lines += [
        "",
        "You can ask me to adjust any field, explain the loan schedule, or compute depreciation.",
        "All changes are automatically synced to the formal records.",
    ]
    intro_text = "\n".join(intro_lines)

    db.add(
        TaskMessage(
            id=str(uuid.uuid4()),
            task_id=task.id,
            sequence_index=0,
            role="assistant",
            content_text=intro_text,
            content_type="text",
        )
    )

    db.commit()
    logger.info(
        "[Other] Created task=%s record=%s type=%s company=%s",
        task.id,
        record.id,
        req.document_subtype,
        company_id,
    )

    return RouteResponse(task_id=task.id, record_id=record.id, task_title=title)


@router.get("/records")
async def list_other_records(
    task_id: str,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
) -> dict:
    """List all OtherRecords for a given task."""
    records = (
        db.query(OtherRecord)
        .filter(
            OtherRecord.task_id == task_id,
            OtherRecord.company_id == company_id,
        )
        .order_by(OtherRecord.created_at)
        .all()
    )
    return {
        "records": [
            {
                "id": r.id,
                "record_type": r.record_type,
                "payload_json": r.payload_json,
                "source_task_id": r.source_task_id,
                "source_file_id": r.source_file_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ]
    }


@router.patch("/records/{record_id}")
async def update_other_record(
    record_id: str,
    req: RecordUpdateRequest,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
) -> dict:
    """
    Update an OtherRecord's payload_json and auto-sync to formal tables.
    """
    record = (
        db.query(OtherRecord)
        .filter(
            OtherRecord.id == record_id,
            OtherRecord.company_id == company_id,
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")

    record.payload_json = {**(record.payload_json or {}), **req.payload_json}
    db.flush()

    # Auto-sync
    sync_record(record, db)

    return {"ok": True, "record_id": record.id}


@router.get("/records/{record_id}/depreciation")
async def get_depreciation_schedule(
    record_id: str,
    db: Session = Depends(get_db),
    company_id: str = Depends(get_current_company_id),
) -> dict:
    """Return the depreciation schedule for a fixed_asset record."""
    record = (
        db.query(OtherRecord)
        .filter(
            OtherRecord.id == record_id,
            OtherRecord.company_id == company_id,
            OtherRecord.record_type == "fixed_asset",
        )
        .first()
    )
    if not record:
        raise HTTPException(status_code=404, detail="Record not found or not a fixed_asset")

    asset = (
        db.query(FixedAsset)
        .filter(FixedAsset.other_record_id == record.id)
        .first()
    )
    if not asset:
        return {"schedule": []}

    schedule_rows = (
        db.query(AssetDepreciationSchedule)
        .filter(AssetDepreciationSchedule.asset_id == asset.id)
        .order_by(AssetDepreciationSchedule.period_start)
        .all()
    )
    return {
        "asset_id": asset.id,
        "asset_name": asset.asset_name,
        "purchase_amount": asset.purchase_amount,
        "residual_value": asset.residual_value,
        "useful_life_months": asset.useful_life_months,
        "depreciation_method": asset.depreciation_method,
        "accumulated_depreciation": asset.accumulated_depreciation,
        "net_book_value": asset.net_book_value,
        "schedule": [
            {
                "period_number": i + 1,
                "period_start": r.period_start.isoformat()[:10] if r.period_start else None,
                "period_end": r.period_end.isoformat()[:10] if r.period_end else None,
                "period_type": r.period_type,
                "depreciation_amount": r.depreciation_amount,
                "accumulated_at_period_end": r.accumulated_at_period_end,
                "net_book_value_at_period_end": r.net_book_value_at_period_end,
            }
            for i, r in enumerate(schedule_rows)
        ],
    }

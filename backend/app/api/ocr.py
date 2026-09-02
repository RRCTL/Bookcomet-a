import asyncio
import contextlib
import contextvars
import logging
import os
import tempfile
import traceback
import uuid
import re
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile, Form, Depends
from sqlalchemy.orm import Session

from app.core.config import resolve_layout_classify_model, resolve_settings_vlm_model, settings
from app.ocr.interfaces import OcrLine, OcrResult
from app.ocr.runtime import (
    BANK_VLM_MODEL,
    ai_processor as _ai_processor,
    filtering_pipeline as _filtering_pipeline,
    ocr_service as _ocr_service,
)
from app.database import get_db, SessionLocal
from app.api.deps import get_current_company_id, get_current_user, get_trace_id
from app.models.identity import User
from app.services.file_storage import assert_file_type, assert_upload_size
from app.core.db_concurrency import long_running_db_work_slot
from app.models.company_context import CompanyProfile
from app.models.compliance import OcrCompletionEvent
from app.models.background_job import BackgroundJob
from app.models.rule_memory import CompanyRuleMemory

# Only document OCR modes load rule memory into the VLM/deterministic OCR pipeline.
_OCR_RULE_MEMORY_MODES = frozenset({"AR", "AP", "BANK", "OTHER"})
from app.services.rule_memory_parser import (
    apply_rules_to_rows as _apply_rules_from_memory,
    extract_ai_instructions as _extract_ai_instructions,
)
from app.services.exclusion_service import apply_exclusion_rules_to_rows as _apply_exclusions
from app.services.re_vlm_hints import (
    build_rescan_prompt_block,
    normalize_expected_receipt_count,
    resolve_expected_receipt_count,
    validate_rescan_reasons,
)
from app.services.chart_of_accounts import get_prompt_account_lines
from app.services.decision_evidence import build_decision_evidence
from app.services.document_gate import (
    classify_document,
    infer_document_subtype,
    TRANSACTIONAL,
    REFERENCE_FINANCIAL,
    NON_FINANCIAL,
    AMBIGUOUS,
    GATE_MESSAGES,
)
from app.services.abuse_guard import (
    company_ocr_concurrency,
    check_monthly_cost,
    sanitise_ocr_text,
)
from app.services.job_tasks import OcrBackgroundJobCancelled, background_job_cancelled
from app.services import extraction_validation as _extraction_validation
from app.services import receipt_image_quality as _receipt_image_quality
from app.ocr import cross_check as _ocr_cross_check
from app.ocr.vlm_layout_detect import (
    VLM_RECEIPT_DETECT_PROMPT,
    is_vlm_detection_backend,
    parse_vlm_detect_regions,
    receipt_instance_id,
    resolve_ap_detection_backend,
    vlm_split_review_payload,
)
from app.services.ap_vlm_cross_merge import (
    _min_tsv_confidence,
    cross_extraction_passes_confidence_gate,
    merge_ap_ai_enhanced_primary_with_cross,
)

def _detect_document_type(ocr_text: str) -> str:
    """Best-effort document type detection based on OCR text (generic rules)."""
    if not ocr_text:
        return "cheque"

    text = ocr_text.lower()

    invoice_keywords = ("invoice", "發票", "发票", "invoice no", "invoice number", "inv no")
    bank_keywords = ("bank statement", "statement", "對帳單", "对账单", "結單", "结单")
    receipt_keywords = ("receipt", "收據", "收据")
    cheque_keywords = ("cheque", "check", "支票")

    if any(k in text for k in invoice_keywords):
        return "invoice"
    if any(k in text for k in bank_keywords):
        return "bank_statement"
    if any(k in text for k in receipt_keywords):
        return "receipt"
    if any(k in text for k in cheque_keywords):
        return "cheque"

    return "cheque"


def _document_type_for_enhancement(
    processing_mode: str,
    ocr_text: str,
    *,
    page_num: int | None = None,
) -> str:
    """Pick AI document_type; BANK must not fall through to generic cheque detection."""
    mode = (processing_mode or "").upper()
    if mode == "BANK":
        return "bank_statement_page" if page_num is not None else "bank_statement"
    return _detect_document_type(ocr_text)


def _is_cheque_document(ocr_text: str) -> bool:
    """
    Strong cheque signals only (empty text => False, unlike _detect_document_type default).
    """
    if not ocr_text or not ocr_text.strip():
        return False
    low = ocr_text.lower()
    if "支票" in ocr_text or "cheque" in low:
        return True
    if re.search(r"(?<![\w-])chq(?![\w-])", low, re.IGNORECASE):
        return True
    if "祈付" in ocr_text or "or bearer" in low:
        return True
    if "h.k. dollars" in low or "h.k dollars" in low or "not negotiable" in low:
        return True
    if "中國銀行" in ocr_text or "bank of china" in low:
        return True
    if "pay" in low and re.search(
        r"payee|pay to|本票|劃線",
        ocr_text,
        re.IGNORECASE,
    ):
        return True
    return False


router = APIRouter()
logger = logging.getLogger(__name__)

_ap_cross_verify_force_cv: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "ap_cross_verify_force", default=False
)
_workflow_run_id_cv: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "workflow_run_id", default=None
)


class WorkflowRunCancelled(Exception):
    """Workflow run was stopped while OCR was in progress."""


def _raise_if_bg_job_cancelled(job_id: str | None) -> None:
    if job_id and background_job_cancelled(job_id):
        raise OcrBackgroundJobCancelled()
    wf_id = _workflow_run_id_cv.get()
    if wf_id:
        from app.graph.workflow_service import workflow_run_should_abort_processing

        if workflow_run_should_abort_processing(wf_id):
            raise WorkflowRunCancelled()


def _persist_background_job_partial_result(
    *,
    job_id: str | None,
    result_json: dict[str, Any],
    progress_percent: int | None = None,
    progress_label: str | None = None,
) -> None:
    """Persist mid-flight OCR snapshots so frontend polling can render progressively."""
    if not job_id:
        return
    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if not job:
            return
        if job.status in ("failed", "completed", "cancelled"):
            return
        job.result_json = result_json
        if progress_percent is not None:
            job.progress_percent = str(max(0, min(99, int(progress_percent))))
        if progress_label:
            job.progress_label = progress_label
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("[OCR Partial] failed to persist partial snapshot for job %s", job_id)
    finally:
        db.close()


async def _poll_cancel_tasks(
    tracked: list[asyncio.Task[Any]],
    *,
    job_id: str | None,
    interval_s: float = 0.35,
) -> None:
    """Cancel all tracked tasks when the background job row is marked cancelled."""
    if not job_id or not tracked:
        return
    try:
        while True:
            await asyncio.sleep(interval_s)
            if background_job_cancelled(job_id):
                for t in tracked:
                    if not t.done():
                        t.cancel()
                return
    except asyncio.CancelledError:
        raise



from app.utils.file_converter import (
    convert_pdf_to_images_list,
    convert_one_pdf_page_to_temp_png,
    pdf_document_page_count,
)

try:
    import fitz  # PyMuPDF
    PDF_SUPPORT = True
    logger.info("[OK] PyMuPDF PDF support enabled")
except ImportError as e:
    PDF_SUPPORT = False
    logger.warning(f"[WARN] PyMuPDF not installed. PDF support disabled. Error: {e}")
except Exception as e:
    PDF_SUPPORT = False
    logger.error(f"[ERROR] Unexpected error loading PDF support: {e}", exc_info=True)

BANK_TABLE_PARSING_PROMPT = """
You are an OCR engine for Hong Kong bank statements.

Task:
Transcribe the WHOLE page into HTML (not table-only).
You must capture:
1) Statement header area (bank name, company/account holder name, account info, statement period/date, page number)
2) Transaction tables
3) Relevant footer/summary text visible on the page

Output requirements:
- Output HTML only (no markdown, no explanation).
- Preserve reading order from top to bottom.
- Keep full-page structure using semantic sections when possible:
  - <div class="statement-header">...</div>
  - <table>...</table> for transaction grids
  - <div class="statement-footer">...</div>
- For table content, use <table>, <tr>, <td> and keep row/column alignment.
- Keep merged cells by preserving content in the closest logical cell.
- Do NOT omit header lines even if they are not part of a table.
- Do NOT normalize away key labels (e.g., bank name, account holder/company name, page info).

Quality rules:
- Prefer faithful OCR transcription over guessing.
- If text is uncertain, keep best-effort text in place instead of dropping it.
"""

# BANK prompts are now managed in app/bank_prompts/ (one file per bank).
# BANK_VLM_JSON_PROMPT has been removed from here and lives in bank_prompts/default.py.

AR_AP_HTML_OCR_PROMPT = """
You are an OCR engine for Hong Kong accounting documents (invoice/receipt).

Task:
Transcribe the WHOLE page into HTML (not plain text).
You must capture:
1) Document header area (seller/buyer, invoice/receipt number, date, currency)
2) Item/amount sections (line items, subtotal, tax, total)
3) Footer and references visible on the page

Output requirements:
- Output HTML only (no markdown, no explanation).
- Preserve reading order from top to bottom.
- Keep full-page structure using semantic sections when possible:
  - <div class="doc-header">...</div>
  - <table>...</table> for item/amount grids
  - <div class="doc-footer">...</div>
- For table-like content, use <table>, <tr>, <td> and keep row/column alignment.
- Keep labels and numbers faithfully (do not rewrite values).

Quality rules:
- Prefer faithful OCR transcription over guessing.
- If text is uncertain, keep best-effort text in place instead of dropping it.
"""

# AP payables: VLM id for both passes (document OCR + structured JSON).
# Env order: AP_VLM_MODEL → AP_MULTI_RECEIPT_OCR_MODEL (legacy) → Settings VLM_MODEL.
def resolve_ap_vlm_model() -> str:
    return resolve_settings_vlm_model(
        os.getenv("AP_VLM_MODEL"),
        os.getenv("AP_MULTI_RECEIPT_OCR_MODEL"),
    )


def resolve_ar_ocr_model() -> str:
    # AR_OCR_MODEL if set; else same chain as AP (including Settings VLM_MODEL).
    return resolve_settings_vlm_model(
        os.getenv("AR_OCR_MODEL"),
        os.getenv("AP_VLM_MODEL"),
        os.getenv("AP_MULTI_RECEIPT_OCR_MODEL"),
    )


AP_VLM_MODEL = resolve_ap_vlm_model()
# Backward-compatible name used throughout this module (same resolved string as AP_VLM_MODEL).
AP_MULTI_RECEIPT_OCR_MODEL = AP_VLM_MODEL
# Optional second VLM for manual AP "Double check" only (see POST /api/tasks/.../ap-cross-verify).
AP_CROSS_VLM_MODEL = os.getenv("AP_CROSS_VLM_MODEL", "").strip()

# Model used for AR mode OCR & AI extraction.
AR_OCR_MODEL = resolve_ar_ocr_model()

# AP multi-receipt: optional VLM-first page layout (legacy flag; AP/AR crop uses Settings VLM Detect).
_AP_VLM_LAYOUT_FLAG = os.getenv("AP_VLM_LAYOUT_CROP_ENABLED", "").strip().lower()
AP_VLM_LAYOUT_CROP_ENABLED = _AP_VLM_LAYOUT_FLAG in ("1", "true", "yes", "on")
# vlm = Settings VLM Detect (default). opencv is not used for AP/AR receipt crop.
AP_DETECTION_BACKEND = resolve_ap_detection_backend()
try:
    AP_VLM_LAYOUT_CONFIDENCE_MIN = float(os.getenv("AP_VLM_LAYOUT_CONFIDENCE_MIN", "0.75"))
except ValueError:
    AP_VLM_LAYOUT_CONFIDENCE_MIN = 0.75
try:
    AP_VLM_LAYOUT_THUMB_MAX_SIDE = int(os.getenv("AP_VLM_LAYOUT_THUMB_MAX_SIDE", "800"))
except ValueError:
    AP_VLM_LAYOUT_THUMB_MAX_SIDE = 800
try:
    AP_VLM_LAYOUT_BOX_PAD_PCT = float(os.getenv("AP_VLM_LAYOUT_BOX_PAD_PCT", "0.02"))
except ValueError:
    AP_VLM_LAYOUT_BOX_PAD_PCT = 0.02
try:
    AP_CROP_OCR_CONCURRENCY = max(1, int(os.getenv("AP_CROP_OCR_CONCURRENCY", "8")))
except ValueError:
    AP_CROP_OCR_CONCURRENCY = 8
try:
    AP_CROP_OCR_IMAGE_MAX_SIDE = max(0, int(os.getenv("AP_CROP_OCR_IMAGE_MAX_SIDE", "0")))
except ValueError:
    AP_CROP_OCR_IMAGE_MAX_SIDE = 0
try:
    AP_CROP_OCR_JPEG_QUALITY = int(os.getenv("AP_CROP_OCR_JPEG_QUALITY", "90"))
except ValueError:
    AP_CROP_OCR_JPEG_QUALITY = 90
AP_CROP_OCR_JPEG_QUALITY = max(1, min(100, AP_CROP_OCR_JPEG_QUALITY))
try:
    _layout_jq = int(os.getenv("AP_VLM_LAYOUT_JPEG_QUALITY", "88"))
except ValueError:
    _layout_jq = 88
AP_VLM_LAYOUT_JPEG_QUALITY = max(1, min(100, _layout_jq))
try:
    AP_VLM_LAYOUT_MAX_RETRIES = max(0, int(os.getenv("AP_VLM_LAYOUT_MAX_RETRIES", "1")))
except ValueError:
    AP_VLM_LAYOUT_MAX_RETRIES = 1
try:
    AP_STITCH_UPLOAD_MIN_SHORT_EDGE = max(1, int(os.getenv("AP_STITCH_UPLOAD_MIN_SHORT_EDGE", "480")))
except ValueError:
    AP_STITCH_UPLOAD_MIN_SHORT_EDGE = 480
try:
    AP_LAYOUT_LAST_PAGE_MIN_PAGES = max(2, int(os.getenv("AP_LAYOUT_LAST_PAGE_MIN_PAGES", "5")))
except ValueError:
    AP_LAYOUT_LAST_PAGE_MIN_PAGES = 5
try:
    AP_LAYOUT_MAX_PAGE_CONCURRENCY = max(1, int(os.getenv("AP_LAYOUT_MAX_PAGE_CONCURRENCY", "3")))
except ValueError:
    AP_LAYOUT_MAX_PAGE_CONCURRENCY = 3
_ap_layout_early_reasons = os.getenv(
    "AP_VLM_LAYOUT_EARLY_FALLBACK_REASONS",
    "fewer_than_two_regions,box_too_small,invalid_box_fields,box_out_of_range,box_overflow",
)
AP_VLM_LAYOUT_EARLY_FALLBACK_REASONS = frozenset(
    s.strip() for s in _ap_layout_early_reasons.split(",") if s.strip()
)
try:
    OCR_SCENARIO_D_MAX_CONSECUTIVE_FAILURES = max(
        0, int(os.getenv("OCR_SCENARIO_D_MAX_CONSECUTIVE_FAILURES", "12"))
    )
except ValueError:
    OCR_SCENARIO_D_MAX_CONSECUTIVE_FAILURES = 12
try:
    OCR_SCENARIO_D_MAX_FAILURE_RATIO = float(os.getenv("OCR_SCENARIO_D_MAX_FAILURE_RATIO", "0.70"))
except ValueError:
    OCR_SCENARIO_D_MAX_FAILURE_RATIO = 0.70
try:
    OCR_SCENARIO_D_FAILURE_RATIO_MIN_SAMPLES = max(
        1, int(os.getenv("OCR_SCENARIO_D_FAILURE_RATIO_MIN_SAMPLES", "10"))
    )
except ValueError:
    OCR_SCENARIO_D_FAILURE_RATIO_MIN_SAMPLES = 10
try:
    AP_CROP_MIN_WIDTH_PX = max(1, int(os.getenv("AP_CROP_MIN_WIDTH_PX", "80")))
except ValueError:
    AP_CROP_MIN_WIDTH_PX = 80
try:
    AP_CROP_MIN_HEIGHT_PX = max(1, int(os.getenv("AP_CROP_MIN_HEIGHT_PX", "80")))
except ValueError:
    AP_CROP_MIN_HEIGHT_PX = 80
try:
    AP_CROP_MIN_AREA_PX = max(1, int(os.getenv("AP_CROP_MIN_AREA_PX", "9000")))
except ValueError:
    AP_CROP_MIN_AREA_PX = 9000
try:
    AP_CROP_MIN_ASPECT_RATIO = float(os.getenv("AP_CROP_MIN_ASPECT_RATIO", "0.08"))
except ValueError:
    AP_CROP_MIN_ASPECT_RATIO = 0.08
try:
    AP_CROP_MAX_ASPECT_RATIO = float(os.getenv("AP_CROP_MAX_ASPECT_RATIO", "12.0"))
except ValueError:
    AP_CROP_MAX_ASPECT_RATIO = 12.0
try:
    AP_SEG_MIN_INK_FRACTION = float(os.getenv("AP_SEG_MIN_INK_FRACTION", "0.025"))
except ValueError:
    AP_SEG_MIN_INK_FRACTION = 0.025
AP_SEG_MIN_INK_FRACTION = max(0.0, min(AP_SEG_MIN_INK_FRACTION, 0.5))
try:
    AP_SEG_MULTI_MIN_REGION_AREA_FRAC = float(
        os.getenv("AP_SEG_MULTI_MIN_REGION_AREA_FRAC", "0.06")
    )
except ValueError:
    AP_SEG_MULTI_MIN_REGION_AREA_FRAC = 0.06
try:
    AP_SEG_MULTI_MAX_DOMINANCE = float(os.getenv("AP_SEG_MULTI_MAX_DOMINANCE", "0.78"))
except ValueError:
    AP_SEG_MULTI_MAX_DOMINANCE = 0.78
try:
    AP_SEG_MIN_GAP_FRAC = float(os.getenv("AP_SEG_MIN_GAP_FRAC", "0.012"))
except ValueError:
    AP_SEG_MIN_GAP_FRAC = 0.012
try:
    AP_SEG_FRAGMENT_REL_AREA_MAX = float(os.getenv("AP_SEG_FRAGMENT_REL_AREA_MAX", "0.25"))
except ValueError:
    AP_SEG_FRAGMENT_REL_AREA_MAX = 0.25
try:
    AP_SEG_SINGLE_MERGE_PAD_FRAC = float(os.getenv("AP_SEG_SINGLE_MERGE_PAD_FRAC", "0.01"))
except ValueError:
    AP_SEG_SINGLE_MERGE_PAD_FRAC = 0.01

AP_VLM_RECEIPT_SIGNAL_VALUES = frozenset(
    {"guess", "single_per_page", "multi_per_page", "single_span_pages"}
)
AP_VLM_TABLE_PRESET_VALUES = frozenset({"default", "ap_table"})

AP_VLM_AP_TABLE_COLUMN_HINT = (
    "When structuring extracted payables data, prefer field names aligned with this column set "
    "(map OCR labels semantically): "
    "id_number, matched_id, source_file, invoice_date, due_date, invoice_number, "
    "vendor_name, vendor_tax_id, total_amount, tax_amount, currency, account_code, category, "
    "description, payment_status, confidence."
)

_AP_VLM_RECEIPT_SIGNAL_HINTS: dict[str, str] = {
    "guess": "No explicit receipt-layout preference from the user; infer layout normally.",
    "single_per_page": (
        "User expects at most one receipt per page (avoid splitting one page into multiple slips)."
    ),
    "multi_per_page": (
        "User expects multiple separate receipts may appear on the same page; segment carefully."
    ),
    "single_span_pages": (
        "User expects one receipt or invoice may span multiple consecutive pages; "
        "treat as one logical document when stitching applies."
    ),
}

AP_VLM_LAYOUT_DETECTION_PROMPT = """You are analysing a scanned page that may contain multiple separate receipt or payment slips.
Text may be in any language (e.g. English, Chinese, Japanese). Focus on visual boundaries between distinct slips.

Respond with ONLY a single JSON object (no markdown fences, no commentary) in exactly this shape:
{
  "confidence": <number between 0 and 1, your certainty that the boxes are accurate>,
  "count": <integer, number of distinct receipt/slip regions>,
  "receipts": [
    { "x": <number>, "y": <number>, "w": <number>, "h": <number> }
  ],
  "rows": <integer or null>,
  "cols": <integer or null>
}

Rules for "receipts":
- Each object uses NORMALIZED coordinates relative to the image: x,y are top-left; w,h are width and height.
- All values are fractions of the full image: 0 <= x < 1, 0 <= y < 1, 0 < w <= 1-x, 0 < h <= 1-y.
- Include every separate slip; do not merge distinct receipts into one box.
- If only one receipt covers the page, set count to 1 and one box covering the slip (not necessarily the full page margin).
"""

AP_VLM_LAYOUT_DETECTION_PROMPT_REPAIR = """You are analysing a scanned page that may contain multiple separate receipt or payment slips.
A previous JSON answer for this image was missing, invalid, or internally inconsistent.

Respond with ONLY a single JSON object (no markdown fences, no commentary) in exactly this shape:
{
  "confidence": <number between 0 and 1, your certainty that the boxes are accurate>,
  "count": <integer, number of distinct receipt/slip regions>,
  "receipts": [
    { "x": <number>, "y": <number>, "w": <number>, "h": <number> }
  ],
  "rows": <integer or null>,
  "cols": <integer or null>
}

Mandatory consistency:
- The integer "count" MUST equal the number of elements in "receipts" (same value, no off-by-one).

Rules for "receipts":
- Each object uses NORMALIZED coordinates: x,y top-left; w,h width and height; 0 <= x,y,x+w,y+h <= 1.
- Include every visually separate slip; do not merge distinct receipts. Include small or partly overlapping slips as separate boxes when they are separate documents.
- Text may be in any language.
"""

AP_MULTI_RECEIPT_DOCUMENT_PARSING_PROMPT = """
You are a document parsing OCR model for Hong Kong AP receipts.

Task:
- Parse the cropped receipt image into plain text for downstream field extraction.
- Preserve all structure and semantics: merchant name, address, date, line items, totals, payment details.
- Keep reading order (top to bottom, left to right) and proximity relationships.

Critical rules:
- ALWAYS transcribe the total amount line completely, e.g. "總金額 $326.70" or "TOTAL HKD 277.00".
  Do not abbreviate or drop the dollar sign or the number.
- For dates, transcribe exactly as printed (e.g. "16/11/2023" or "2024-03-27"). HK receipts use DD/MM/YYYY.
- Transcribe the merchant name from the top of the receipt as the first line.
- If the receipt has a payment card section (RRN / TRACE / approval code), include it fully.

Output requirements:
- Output plain text only (no markdown, no HTML, no explanation).
- Keep key-value style labels and table/line structure faithful to the image.

If the image is a Hong Kong bank CHEQUE (not a POS receipt):
- Transcribe the bank name, PAY/祈付 line and payee name, amount in words and figures, date,
  drawer (payer) and payee as printed, crossing / "NOT NEGOTIABLE" if visible, and
  the cheque number (often at the bottom, sometimes in quotes or MICR-style digits).
- Preserve the reading order; do not treat the payee as a receipt "merchant" line.
"""

# Cheque-only VLM: router quick-probe + 4-way orientation (do not use for AP receipt parsing).
HK_CHEQUE_DETECTION_TRANSCRIPTION_PROMPT = """
You are transcribing a photograph of a Hong Kong BANK CHEQUE (a negotiable instrument: 支票, drawn on a bank). This is NOT a shop/POS receipt—do not describe a merchant, 總金額, RRN, or card payment unless those words are literally on the cheque.

Task: Transcribe ALL visible text in natural reading order (top to bottom). Preserve Chinese and English as printed. Include:
- Full bank / branch name and any legal line (e.g. 中國銀行, BANK OF CHINA, (HONG KONG) …).
- The PAY / 祈付 line, payee name, and the words OR BEARER / 或持票人 if present.
- Face value in words and in figures, including HK$ / H.K. DOLLARS / 港幣.
- Printed date in any format shown.
- Drawer / payer (signatory or account name) if visible, but do NOT use the bank's name as payee.
- Cheque serial number, often at the bottom, sometimes in quotes; include any long digit or MICR-like line.
- Crossings, NOT NEGOTIABLE, 劃線, 本票, or other printed security text.

If the image is clearly a retail receipt or invoice only (not a bank cheque), output exactly this single line and nothing else:
NOT_A_BANK_CHEQUE

Otherwise output plain text only—no JSON, no markdown, no explanation.
""".strip()


def _is_not_a_bank_cheque_sentinel(ocr_text: str) -> bool:
    """VLM may decline when the page is not a bank cheque; treat as not matching cheque routing."""
    return (ocr_text or "").strip().upper() == "NOT_A_BANK_CHEQUE"


def _is_cheque_deposit_advice(ocr_text: str) -> bool:
    """Detect bank-in slips / cheque deposit advice before standalone cheque routing."""
    if not ocr_text or not ocr_text.strip():
        return False
    low = ocr_text.lower()
    strong_markers = (
        "cheque deposit advice",
        "存支票機",
        "no. of cheque(s) accepted",
        "no. of cheques accepted",
    )
    if any(k in low or k in ocr_text for k in strong_markers):
        return True
    return (
        ("account no" in low or "賬戶號碼" in ocr_text or "账户号码" in ocr_text)
        and ("total" in low or "金額總數" in ocr_text or "金额总数" in ocr_text)
        and ("cheque" in low or "支票" in ocr_text)
    )


# Cheque: portrait / mis-rotated capture – optional 4-way orientation probe (cheque VLM path only).
_CHEQUE_AUTO_ROT_E = os.getenv("CHEQUE_AUTO_ROTATE_ENABLED", "1").strip().lower()
CHEQUE_AUTO_ROTATE_ENABLED = _CHEQUE_AUTO_ROT_E in ("1", "true", "yes", "on")
try:
    CHEQUE_ORIENTATION_PROBE_MAX_SIDE = max(256, min(1024, int(os.getenv("CHEQUE_ORIENTATION_PROBE_MAX_SIDE", "512"))))
except ValueError:
    CHEQUE_ORIENTATION_PROBE_MAX_SIDE = 512
_CHEQUE_ROUTER_P_E = os.getenv("CHEQUE_ROUTER_QUICK_PROBE_ENABLED", "1").strip().lower()
CHEQUE_ROUTER_QUICK_PROBE_ENABLED = _CHEQUE_ROUTER_P_E in ("1", "true", "yes", "on")
try:
    CHEQUE_ROUTER_PROBE_MAX_SIDE = max(256, min(1024, int(os.getenv("CHEQUE_ROUTER_PROBE_MAX_SIDE", "512"))))
except ValueError:
    CHEQUE_ROUTER_PROBE_MAX_SIDE = 512


def _empty_cheque_probe_result() -> dict[str, Any]:
    return {"matched": False, "text": "", "degrees": None, "score": 0.0}


async def _ar_ap_cheque_router_quick_probe(
    image_path: str,
    ocr_provider_name: str,
    ocr_model_override: Optional[str],
) -> dict[str, Any]:
    """
    When the layout classifier said 'receipts', run a small VLM read to detect
    a HK bank cheque and skip OpenCV multi-receipt segmentation (Scenario B).

    If CHEQUE_AUTO_ROTATE_ENABLED, probes 0/90/180/270 and returns the best
    matching orientation so cheque extraction does not repeat the same probe.
    Otherwise a single pass on the original file.
    """
    if not CHEQUE_ROUTER_QUICK_PROBE_ENABLED or not image_path or not os.path.isfile(image_path):
        return _empty_cheque_probe_result()
    probe_options: dict = {
        "max_side": CHEQUE_ROUTER_PROBE_MAX_SIDE,
        "format": "JPEG",
        "quality": 85,
    }
    model = ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL

    async def _one_vlm(path: str) -> str:
        r = await _ocr_service.recognize(
            path,
            provider_name=ocr_provider_name,
            model=model,
            prompt_override=HK_CHEQUE_DETECTION_TRANSCRIPTION_PROMPT,
            ocr_options={"temperature": 0.1},
            image_options=probe_options,
        )
        return sanitise_ocr_text(r.text or "")

    if not CHEQUE_AUTO_ROTATE_ENABLED:
        try:
            t = await _one_vlm(image_path)
            if _is_not_a_bank_cheque_sentinel(t) or not _is_cheque_document(t):
                return _empty_cheque_probe_result()
            score = _score_cheque_orientation_probe(t)
            logger.info(
                "[ROUTER] AR/AP cheque quick-probe: match (score=%.1f text_len=%s) — override layout=receipts to Scenario A",
                score, len(t),
            )
            return {"matched": True, "text": t, "degrees": None, "score": score}
        except Exception as e:
            logger.warning("[ROUTER] AR/AP cheque quick-probe failed: %s", e)
            return _empty_cheque_probe_result()

    variants, all_temps = _build_cheque_orientation_variants(image_path)
    best_text = ""
    best_deg: int | None = None
    best_score = -1.0
    try:
        probe_texts = await asyncio.gather(
            *(_one_vlm(path) for path, _deg in variants), return_exceptions=True
        )
        for (_path, deg), t in zip(variants, probe_texts):
            if isinstance(t, BaseException):
                logger.warning("[ROUTER] cheque quick-probe orientation %s° failed: %s", deg, t)
                continue
            if _is_not_a_bank_cheque_sentinel(t) or not _is_cheque_document(t):
                continue
            score = _score_cheque_orientation_probe(t)
            if score > best_score:
                best_score = score
                best_deg = deg
                best_text = t
        if best_deg is None:
            return _empty_cheque_probe_result()
        logger.info(
            "[ROUTER] AR/AP cheque quick-probe: best match at %s° (score=%.1f text_len=%s) — override to Scenario A",
            best_deg, best_score, len(best_text),
        )
        return {"matched": True, "text": best_text, "degrees": best_deg, "score": best_score}
    except Exception as e:
        logger.warning("[ROUTER] AR/AP cheque quick-probe failed: %s", e)
        return _empty_cheque_probe_result()
    finally:
        for p in all_temps:
            try:
                os.unlink(p)
            except OSError:
                pass


def _score_cheque_orientation_probe(ocr_text: str) -> float:
    """Higher score = better match for a readable HK cheque (used to pick 0/90/180/270)."""
    t = ocr_text or ""
    if not t.strip():
        return 0.0
    score = float(len(t.strip()))
    low = t.lower()
    if _is_cheque_document(t):
        score += 5000.0
    for kw in (
        "中國銀行", "bank of china", "祈付", "or bearer", "h.k. dollars",
        "not negotiable",
    ):
        if kw in low or kw in t:
            score += 100.0
    if re.search(r"HK\$\s*[\d,]+", t, re.IGNORECASE) or re.search(
        r"H\.?\s*K\.?\s*DOLLARS", t, re.IGNORECASE
    ):
        score += 200.0
    if re.search(r"\d{5,10}", t.replace(" ", "")):
        score += 50.0
    return score


def _build_cheque_orientation_variants(
    source_path: str,
) -> tuple[list[tuple[str, int]], list[str]]:
    """Build four JPEGs: 0°, 90°, 180°, 270° (after EXIF transpose). Returns (variants, all_temp_paths)."""
    from PIL import Image, ImageOps

    temp_paths: list[str] = []
    with Image.open(source_path) as im:
        base = ImageOps.exif_transpose(im)
    if base.mode not in ("RGB", "L"):
        base = base.convert("RGB")
    out: list[tuple[str, int]] = []
    for deg in (0, 90, 180, 270):
        rot = base if deg == 0 else base.rotate(-deg, expand=True, fillcolor="white")
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".chq-orient.jpg")
        p = tmp.name
        tmp.close()
        rot.save(p, format="JPEG", quality=88, optimize=True)
        temp_paths.append(p)
        out.append((p, deg))
    return out, temp_paths


def _build_cheque_orientation_variant(source_path: str, degrees: int) -> str:
    """Build one full-resolution rotated JPEG using the same rotation convention as the probe."""
    from PIL import Image, ImageOps

    with Image.open(source_path) as im:
        base = ImageOps.exif_transpose(im)
    if base.mode not in ("RGB", "L"):
        base = base.convert("RGB")
    deg = degrees % 360
    rot = base if deg == 0 else base.rotate(-deg, expand=True, fillcolor="white")
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".chq-orient.jpg")
    p = tmp.name
    tmp.close()
    rot.save(p, format="JPEG", quality=88, optimize=True)
    return p


async def _pick_best_cheque_image_orientation(
    source_path: str,
    ocr_text_hint: str,
    ocr_provider_name: str,
    ocr_model_override: str,
    page_num: int,
) -> tuple[str, str, list[str], int | None]:
    """
    When CHEQUE_AUTO_ROTATE_ENABLED, OCR-probe 0/90/180/270 and return best (path, text, paths_to_delete, degrees).
    Disabled or on error: (source_path, ocr_text_hint, [], None).
    """
    if not CHEQUE_AUTO_ROTATE_ENABLED or not source_path or not os.path.isfile(source_path):
        return source_path, ocr_text_hint, [], None
    variants, all_temps = _build_cheque_orientation_variants(source_path)
    probe_options: dict = {
        "max_side": CHEQUE_ORIENTATION_PROBE_MAX_SIDE,
        "format": "JPEG",
        "quality": 85,
    }
    model = ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL
    best_path: str | None = None
    best_deg = 0
    best_text = ""
    best_score = -1.0

    async def _probe(path: str) -> str:
        r = await _ocr_service.recognize(
            path,
            provider_name=ocr_provider_name,
            model=model,
            prompt_override=HK_CHEQUE_DETECTION_TRANSCRIPTION_PROMPT,
            ocr_options={"temperature": 0.1},
            image_options=probe_options,
        )
        return sanitise_ocr_text(r.text or "")

    try:
        probe_texts = await asyncio.gather(
            *(_probe(path) for path, _deg in variants), return_exceptions=True
        )
        for (path, deg), t in zip(variants, probe_texts):
            if isinstance(t, BaseException):
                logger.warning("[Chq orient] probe deg=%s failed: %s", deg, t)
                continue
            if _is_not_a_bank_cheque_sentinel(t):
                continue
            s = _score_cheque_orientation_probe(t)
            if s > best_score:
                best_score = s
                best_path = path
                best_deg = deg
                best_text = t
        if best_path is None:
            for p in all_temps:
                try:
                    os.unlink(p)
                except OSError:
                    pass
            return source_path, ocr_text_hint, [], None
        for p in all_temps:
            if p != best_path:
                try:
                    os.unlink(p)
                except OSError:
                    pass
        logger.info(
            "[Chq page %s] orientation probe: using %s deg score=%.1f text_len=%s",
            page_num, best_deg, best_score, len(best_text),
        )
        return best_path, best_text, [best_path], best_deg
    except Exception:
        for p in all_temps:
            try:
                os.unlink(p)
            except OSError:
                pass
        raise


AP_MULTI_RECEIPT_STRUCTURED_OCR_PROMPT_TEMPLATE = """
You are a financial data extraction assistant for Hong Kong receipts.
Analyze the provided image of a single receipt and extract ONLY the fields below.
Return a single valid JSON object. Use null for absent or ambiguous fields. Do NOT guess.
Output ONLY the JSON object — no commentary, no markdown fences.

Field definitions and extraction rules:
- receipt_id: The receipt/invoice/order/trace number. Look for: 單號, 帳單, Trace, Invoice No, Receipt No, INVNUM.
- transaction_date: The date in YYYY-MM-DD format. Look for: 日期, Date, SALE DATE.
  Hong Kong receipts commonly use DD/MM/YYYY (e.g. "16/11/2023" → "2023-11-16").
  Also convert MM/DD/YYYY or "Sep 22, 2023" style.
- merchant_name: The business name — usually the largest text at the TOP of the receipt, not the address.
  Example: for "KPay 收據 Receipt 80后茶檔 80'S CAFE …" → "80后茶檔"
  Example: for "42 一豐味(葵涌) 顧客單據 檯號: 42 …" → "一豐味(葵涌)"
- total_amount: The FINAL amount charged, as a plain number only (e.g. 326.70, NOT "HKD326.70").
  Priority: 總金額 > 總計 > 合計 > 總數 > 結帳總額 > 應付金額 > GRAND TOTAL > TOTAL > SALES.
  Use the amount AFTER tax and service charge. Do NOT use 優惠總金額 (that is a discount).
  Amount labels are often followed by $ or HK$ before the number, e.g. "總金額 $326.70".
- currency: Currency code (e.g. HKD). Default to HKD if not stated.
- payment_method: How the customer paid. Look for: Mastercard, Visa, Cash, 現金, KPay, 八達通, Octopus, WISPAY, Alipay, PayMe.
- confidence: Your confidence in the extraction as a number from 0 to 100.
- tax_amount: Tax / VAT portion as a plain number, or null if not printed as a separate line.
- subtotal_amount: Pre-tax subtotal before tax, or null if not stated separately from total_amount.

Structured OCR line regions (pixels on image; bbox x1,y1,x2,y2 then text — optional context):
{layout_hint_block}

OCR text hint (use as supporting context; visual image takes priority):
{ocr_text_hint}

JSON schema to return:
{{
  "receipt_id": "string or null",
  "transaction_date": "YYYY-MM-DD or null",
  "merchant_name": "string or null",
  "total_amount": "number or null",
  "tax_amount": "number or null",
  "subtotal_amount": "number or null",
  "currency": "string or null",
  "payment_method": "string or null",
  "confidence": "number (0-100)"
}}
"""

# Appended only to the structured receipt prompt when AP cross-VLM (second pass) runs.
AP_STRUCTURED_CROSS_VERIFY_SUPPLEMENT = """

Verifier pass — multilingual and currency (overrides HK-only defaults above for this request):
- The slip may be in any language or mixed languages. Do not assume Hong Kong or HKD from context alone.
- Set "currency" to a 3-letter ISO 4217 code when evidence supports it: printed codes (HKD, USD, JPY, EUR, GBP, CNY, TWD, …),
  symbols near totals (HK$, US$, NT$, €, £, ¥ with Japanese or regional context), or tax labels
  (e.g. Japanese 消費税 / 内消費税 / 税込 → JPY; EU VAT / TVA / IVA / MWST patterns → local currency such as EUR).
- Use null for "currency" only when evidence is missing or contradictory — do not use null as a substitute for HKD on clearly foreign slips.
- Keep total_amount a plain number without embedding currency symbols in the number string.
"""

AP_MULTI_RECEIPT_STRUCTURED_OCR_PROMPT_FALLBACK = """
You are a financial data extraction assistant for Hong Kong receipts.
Analyze this single receipt image and return ONLY a valid JSON object. No markdown, no commentary.

Extract these fields (use null if absent or uncertain):
- receipt_id: 單號 / 帳單 / Trace / Invoice No / RRN / INVNUM
- transaction_date: YYYY-MM-DD (HK receipts use DD/MM/YYYY — convert accordingly)
- merchant_name: Business name at the top of the receipt, not the address
- total_amount: Final amount as a plain number. Priority: 總金額 > 總計 > 合計 > TOTAL > SALES. NOT 優惠總金額 (discount)
- currency: Default HKD
- payment_method: Mastercard / Visa / Cash / KPay / Octopus / WISPAY / 八達通 / 現金
- confidence: 0-100

Return format:
{"receipt_id": ..., "transaction_date": ..., "merchant_name": ..., "total_amount": ..., "currency": ..., "payment_method": ..., "confidence": ...}
"""

def _build_ap_multi_receipt_structured_prompt(
    ocr_text_hint: str = "",
    layout_hint: str = "",
    *,
    cross_verify: bool = False,
    rescan_supplement: str | None = None,
) -> str:
    # Trim the hint to avoid overloading the context window.
    hint = (ocr_text_hint or "").strip()[:800] or "(not available)"
    layout = (layout_hint or "").strip()
    if len(layout) > 700:
        layout = layout[:700] + "\n…"
    layout_block = layout if layout else "(none)"
    base = AP_MULTI_RECEIPT_STRUCTURED_OCR_PROMPT_TEMPLATE.format(
        ocr_text_hint=hint,
        layout_hint_block=layout_block,
    )
    if cross_verify:
        base = base + AP_STRUCTURED_CROSS_VERIFY_SUPPLEMENT.strip()
    sup = (rescan_supplement or "").strip()
    if sup:
        base = base + "\n\n" + sup
    return base


CHEQUE_STRUCTURED_OCR_PROMPT_TEMPLATE = """
You are a financial data extraction assistant for Hong Kong bank CHEQUES.
Analyze the provided cheque image and return ONLY the fields below as one JSON object.
Use null for absent or uncertain fields. Do NOT guess. Output ONLY the JSON object — no markdown fences, no commentary.

Field rules:
- cheque_number: numeric cheque serial (commonly 6-8 digits), often at the bottom-left MICR area; strip quotes. For Bank of China cheques, prefer the left-bottom quoted number (e.g. "002474"), not the longer bank/account/routing numbers nearby.
- date: YYYY-MM-DD. Convert "27 Jan 2026", DD/MM/YYYY, or other printed date formats.
- payee: name on the PAY/祈付 line (recipient of the cheque).
- payer: drawer / account holder name (who signs or whose account pays); not the bank name.
- amount_numeric: final face value as a plain number (e.g. 10000.00) without currency symbol.
- amount_words: amount in words (English or 中文) if visible.
- currency: usually HKD.
- bank_name: full name of the bank on which the cheque is drawn.
- memo: payment purpose, crossing, "NOT NEGOTIABLE", or other relevant printed notes (short).
- confidence: 0-100, your overall confidence in the extraction.

OCR text hint (supporting only; the image is primary):
{ocr_text_hint}

JSON schema to return:
{{
  "cheque_number": "string or null",
  "date": "YYYY-MM-DD or null",
  "payee": "string or null",
  "payer": "string or null",
  "amount_numeric": "number or null",
  "amount_words": "string or null",
  "currency": "string or null",
  "bank_name": "string or null",
  "memo": "string or null",
  "confidence": "number (0-100) or null"
}}
"""


def _build_cheque_structured_prompt(
    ocr_text_hint: str = "",
    *,
    rescan_supplement: str | None = None,
) -> str:
    hint = (ocr_text_hint or "").strip()[:800] or "(not available)"
    base = CHEQUE_STRUCTURED_OCR_PROMPT_TEMPLATE.format(ocr_text_hint=hint)
    sup = (rescan_supplement or "").strip()
    if sup:
        base = base + "\n\n" + sup
    return base


CHEQUE_DEPOSIT_ADVICE_STRUCTURED_PROMPT_TEMPLATE = """
You are extracting data from a Hong Kong cheque deposit advice / bank-in slip.

This document is NOT a retail receipt and NOT a standalone cheque. It may contain
a small preview image of the deposited cheque, but the main document is the bank
deposit advice.

Extract only the important fields below. Return one valid JSON object only.
Use null when missing or unreadable. Do NOT guess.

Fields:
- bank_name: bank issuing the deposit advice, e.g. BANK OF CHINA (HONG KONG) LIMITED / 中國銀行(香港)有限公司.
- payee_account_number: account number receiving the deposit. Look for Account No. / 賬戶號碼.
- payee_account_name: account holder name near Account No.
- amount: total accepted deposit amount as a plain number. Look for Total / 金額總數.
- currency: currency code, usually HKD.
- date_time: deposit processing date and time. Look for Date & Time / 日期及時間. Convert to YYYY-MM-DD HH:mm when possible.
- cheque_count: number of cheque(s) accepted. Look for No. of Cheque(s) Accepted / 存入支票數目.
- terminal_no: terminal number if visible.
- reference: reference number if visible.
- cheque_number: cheque serial number only if clearly readable from the cheque preview or MICR line. Otherwise null.
- confidence: your confidence as a number from 0 to 100.

Important rules:
- Do not treat the account holder as merchant_name.
- Do not use the bank name as payee_account_name.
- Do not extract card payment fields.
- The amount should come from the deposit advice Total field, not unrelated text unless both match.
- If the cheque preview is too small or blurred, set cheque_number to null.

OCR text hint (supporting only; the image is primary):
{ocr_text_hint}

JSON schema to return:
{{
  "document_type": "cheque_deposit_advice",
  "bank_name": "string or null",
  "payee_account_number": "string or null",
  "payee_account_name": "string or null",
  "amount": "number or null",
  "currency": "string or null",
  "date_time": "YYYY-MM-DD HH:mm or null",
  "cheque_count": "number or null",
  "terminal_no": "string or null",
  "reference": "string or null",
  "cheque_number": "string or null",
  "confidence": "number (0-100) or null"
}}
"""


def _build_cheque_deposit_advice_structured_prompt(
    ocr_text_hint: str = "",
    *,
    rescan_supplement: str | None = None,
) -> str:
    hint = (ocr_text_hint or "").strip()[:1000] or "(not available)"
    base = CHEQUE_DEPOSIT_ADVICE_STRUCTURED_PROMPT_TEMPLATE.format(ocr_text_hint=hint)
    sup = (rescan_supplement or "").strip()
    if sup:
        base = base + "\n\n" + sup
    return base


def _clean_amount(raw: str) -> str:
    """Strip currency symbols, thousand-separators and whitespace from an amount string."""
    import re
    v = raw.strip()
    # Remove common currency prefixes/suffixes: HKD, HK$, USD, $, ¥, £, etc.
    v = re.sub(r"(?i)^(HKD|HK\$|USD|CNY|RMB|JPY|GBP|EUR|AUD|SGD)[\s$]*", "", v)
    v = re.sub(r"(?i)(HKD|HK\$|USD|CNY|RMB|JPY|GBP|EUR|AUD|SGD)\s*$", "", v)
    v = v.replace("$", "").replace("¥", "").replace("£", "").replace("€", "")
    # Remove thousand separators (commas) but keep decimal point.
    v = v.replace(",", "").replace(" ", "")
    # Keep only digits and one decimal point.
    m = re.search(r"\d+(\.\d+)?", v)
    return m.group(0) if m else ""


def _normalize_ap_tsv_row_aliases(row: dict[str, str]) -> dict[str, str]:
    def pick(*keys: str) -> str:
        for key in keys:
            value = row.get(key, "")
            text = str(value).strip()
            if text:
                return text
        return ""

    raw_amount = pick("amount", "金額", "total_amount", "amount_numeric",
                      "total", "sales", "subtotal", "合計", "小計", "結帳總額")
    return {
        "voucher_no": pick("voucher_no", "憑證號", "voucher", "invoice_number", "receipt_no"),
        "transaction_type": pick("transaction_type", "類型", "type", "mode") or "AP",
        "amount": _clean_amount(raw_amount),
        "currency": pick("currency", "幣別", "币别") or "HKD",
        "date": pick("date", "日期"),
        "payer": pick("payer", "付款人", "customer"),
        "payee": pick("payee", "收款人", "vendor", "supplier", "seller"),
        "bank": pick("bank", "銀行", "银行", "bank_name"),
        "category": pick("category", "categorise", "分類", "account_category"),
        "memo": pick("memo", "備註", "备注", "note", "notes"),
        "confidence": pick("confidence", "信心度"),
    }


def _extract_ap_fields_from_text(text: str) -> dict[str, str] | None:
    """
    Extract AP transaction fields directly from the Pass-1 OCR text using
    regex patterns.  This is the primary extraction path — fast, deterministic,
    and does not require an extra API call.

    Returns a normalised field dict, or None if the text has no usable data.
    """
    import re

    t = text or ""

    # ── Amount ────────────────────────────────────────────────────────────────
    # Dollar sign ($) or HK$ may appear between the label and the digits.
    _NUM = r"([0-9]{1,6}(?:[,，][0-9]{3})*(?:\.[0-9]{1,2})?)"
    _GAP = r"[\s:：]*(?:HKD|HK\$|USD|CNY|RMB|JPY|¥|£|€)?[\s$]*"

    # Pre-strip discount/voucher lines so their embedded 金額 labels are not matched.
    # e.g. "優惠總金額: HK$15.00" → removed before scanning
    t_nodiscount = re.sub(
        r"(?:優惠|折扣|Discount|Coupon|Voucher)[^\n]*",
        " ",
        t,
        flags=re.IGNORECASE,
    )

    # Amount-label patterns in strict priority order.
    # Using separate patterns so we scan ALL matches of a higher-priority label
    # before falling back to a lower-priority one.
    _AMT_TIERS = [
        # Tier 1: unambiguous Chinese final totals (after tax / service charge)
        re.compile(r"(?:總金額|結帳總額|應付金額)" + _GAP + _NUM, re.IGNORECASE),
        # Tier 2: common HK final-total labels
        re.compile(r"(?:總計|總數|點菜總數|GRAND\s*TOTAL|TOTAL\s*DUE|TOTAL\s*AMOUNT)" + _GAP + _NUM, re.IGNORECASE),
        # Tier 3: generic English TOTAL / SALES (often the largest line on card slips)
        re.compile(r"(?:TOTAL|SALES)" + _GAP + _NUM, re.IGNORECASE),
        # Tier 4: subtotals — only used if nothing higher found
        re.compile(r"(?:合計|小計|SUBTOTAL|SUB\s*TOTAL|NET\s*AMOUNT)" + _GAP + _NUM, re.IGNORECASE),
        # Tier 5: bare 金額 / AMOUNT — last resort
        re.compile(r"(?:金額|AMOUNT\s*DUE|AMOUNT)" + _GAP + _NUM, re.IGNORECASE),
    ]

    # Bare number on its own line (WISPAY-style receipts with no label at all)
    _BARE_AMOUNT = re.compile(
        r"^\s*(?:HK\$|HKD|USD|\$)?[\s]*([1-9][0-9]{1,5}(?:\.[0-9]{1,2})?)\s*$",
        re.MULTILINE,
    )

    amount = ""
    for tier_pat in _AMT_TIERS:
        # Collect ALL matches at this tier, take the LAST one
        # (final total label typically appears after subtotals in a receipt).
        candidates = []
        for m in tier_pat.finditer(t_nodiscount):
            v = _clean_amount(m.group(1))
            if v:
                try:
                    if float(v.replace(",", "")) > 0:
                        candidates.append(v)
                except ValueError:
                    pass
        if candidates:
            amount = candidates[-1]  # last match = final total
            break

    # Bare-number fallback — pick the LARGEST standalone number (most likely the total)
    if not amount:
        bare_candidates = []
        for m in _BARE_AMOUNT.finditer(t):
            try:
                bare_candidates.append(float(m.group(1).replace(",", "")))
            except ValueError:
                pass
        if bare_candidates:
            amount = f"{max(bare_candidates):.2f}"

    # Terminal receipt fallback (WisPay / POS slips with unlabelled column of numbers)
    # e.g. "WISPAY 1224 CUSTOMER COPY 50.00 0.00 0.00 0.00 50.00 ..."
    # Take the FIRST non-zero amount that appears, which is typically the sale amount.
    if not amount and re.search(r"\b(?:WISPAY|WisPay|EFTPOS|CUSTOMER\s*COPY)\b", t, re.IGNORECASE):
        for m in re.finditer(r"\b([1-9][0-9]{0,5}\.[0-9]{2})\b", t):
            try:
                v = float(m.group(1))
                if v > 0:
                    amount = m.group(1)
                    break
            except ValueError:
                pass

    # ── Currency ──────────────────────────────────────────────────────────────
    currency_m = re.search(r"\b(HKD|HK\$|USD|CNY|RMB|JPY|GBP|EUR|SGD|AUD)\b", t, re.IGNORECASE)
    if currency_m:
        currency = currency_m.group(1).upper()
        if currency == "HK$":
            currency = "HKD"
    elif "£" in t:
        currency = "GBP"
    elif "€" in t:
        currency = "EUR"
    elif re.search(r"NT\$|新台幣", t):
        currency = "TWD"
    elif re.search(r"消費税|內消費税|内消費税|税込", t):
        currency = "JPY"
    else:
        currency = "HKD"

    # ── Date ──────────────────────────────────────────────────────────────────
    date = ""
    month_map = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04",
        "may": "05", "jun": "06", "jul": "07", "aug": "08",
        "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }

    # Pass 1 — YYYY-MM-DD / YYYY/MM/DD (unambiguous)
    m = re.search(r"\b(20\d{2})[/\-](\d{1,2})[/\-](\d{1,2})\b", t)
    if m:
        date = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # Pass 2 — DD/MM/YYYY (HK standard: day ≤31, month ≤12, day usually ≤ month digit space)
    if not date:
        for m in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b", t):
            dd, mm, yyyy = int(m.group(1)), int(m.group(2)), m.group(3)
            if 1 <= dd <= 31 and 1 <= mm <= 12:
                # Disambiguation: if first number > 12 it must be DD; otherwise assume DD/MM
                date = f"{yyyy}-{mm:02d}-{dd:02d}"
                break

    # Pass 3 — "Date: 18/1/2024" or "日期: 2024-02-28" with explicit label (highest trust)
    if not date:
        m = re.search(
            r"(?:日期|DATE|Date)\s*[：:]\s*(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})",
            t, re.IGNORECASE,
        )
        if m:
            dd, mm, yyyy = int(m.group(1)), int(m.group(2)), m.group(3)
            if dd > 12:  # definitely DD/MM/YYYY
                date = f"{yyyy}-{mm:02d}-{dd:02d}"
            else:         # assume DD/MM/YYYY (HK convention)
                date = f"{yyyy}-{mm:02d}-{dd:02d}"

    # Pass 4 — "Sep 22, 2023" / "OCT 14, 2023"
    if not date:
        m = re.search(
            r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(20\d{2})\b",
            t, re.IGNORECASE,
        )
        if m:
            mon = month_map.get(m.group(1).lower()[:3], "01")
            date = f"{m.group(3)}-{mon}-{int(m.group(2)):02d}"

    # Pass 5 — "2024-03-27 10:40:05" already works; also handle YYYYMMDD in OCR timestamps
    if not date:
        m = re.search(r"\b(20\d{2})(\d{2})(\d{2})\b", t)
        if m:
            yyyy, mm, dd = m.group(1), int(m.group(2)), int(m.group(3))
            if 1 <= mm <= 12 and 1 <= dd <= 31:
                date = f"{yyyy}-{mm:02d}-{dd:02d}"

    # ── Voucher / Reference number ────────────────────────────────────────────
    voucher = ""
    for label in ("TRACE", "RRN", "Transaction ID", "Invoice", "Receipt No", "REF"):
        m = re.search(
            rf"{re.escape(label)}\s*[:#：\s]\s*([A-Za-z0-9/\-]+)",
            t, re.IGNORECASE,
        )
        if m:
            candidate = m.group(1).strip()
            if len(candidate) >= 4:
                voucher = candidate
                break

    # ── Payee (merchant name) ─────────────────────────────────────────────────
    payee = ""
    # Priority 1: explicit "Merchant:" label
    m = re.search(r"(?:Merchant|Store|Shop|Vendor)\s*[:\s]+([^\n\r,]{4,60})", t, re.IGNORECASE)
    if m:
        payee = m.group(1).strip()

    # Priority 2: KPay / WisPay receipts — merchant name follows "Receipt" keyword
    if not payee:
        m = re.search(r"(?:KPay|WisPay)\s+收據\s+Receipt\s+(.+?)(?:\s+香港|\s+\d|\s+MID|\s+TID|$)", t, re.IGNORECASE)
        if m:
            payee = m.group(1).strip()[:60]

    # Priority 3: walk lines (or synthesised segments for single-line OCR text)
    if not payee:
        # Split on common hard separators so single-line receipts are chunked
        segments = re.split(r"[\n\r]|(?<=\d{2}:\d{2}:\d{2})\s+|(?=檯號[：:])|(?=帳單[：:])|(?=單號[：:])", t)
        # Patterns that flag a segment as NOT a merchant name
        _noise = re.compile(
            r"""(?x)
            ^\d+$                                    |   # all digits
            ^[\W\d]+$                                |   # all symbols / digits
            \b(?:TEL|FAX|電話|傳真)\b                |   # phone / fax
            \b(?:MID|TID|AID|RRN|TRACE|BATCH|REF)\b |   # terminal IDs
            (?:號舖|樓|地下|商場|大廈|廣場|路|街道|道|Avenue|Road|Street|Floor|LEVEL|SHOP|MALL) |
            (?:TOTAL|小計|合計|總計|金額|AMOUNT|DATE|日期|TIME|時間) |
            (?:THANK\s*YOU|多謝惠顧|感謝惠顧|PAID|COPY|RECEIPT|Customer) |
            (?:顧客單據|服務員|開單|流水號|帳單|員工|人數|檯號|桌號|台號|人員)  # POS system fields / table info
            """,
            re.IGNORECASE,
        )
        for seg in segments[:12]:
            seg = seg.strip()
            if len(seg) < 4:
                continue
            # Strip leading table-number prefix: "42 一豐味(葵涌)" → "一豐味(葵涌)"
            clean = re.sub(r"^\d{1,3}\s+", "", seg).strip()
            if len(clean) < 4:
                continue
            # Only accept if the segment does NOT look like noise
            if not _noise.search(clean):
                payee = clean[:60]
                break
        # Fallback A: extract text BEFORE first address/terminal marker in the first 200 chars
        # Works for "茶悅 葵涌梨木道120號石蔭商場 電話: 25198318..."
        if not payee:
            _addr_start = re.search(
                r"(?:\d{2,}[號号舖]|(?:路|街|道)(?:\d|\s|$)|大廈|商場|廣場|電話\s*[：:]|Tel\s*[:：]|MID\s*[:：]|TID\s*[:：])",
                t[:200],
                re.IGNORECASE,
            )
            if _addr_start:
                pre_text = t[:_addr_start.start()].strip()
                pre_text = re.sub(r"^\d{1,3}\s+", "", pre_text).strip()
                # Accept if ≥2 chars and contains Chinese or uppercase word
                if len(pre_text) >= 2 and (
                    re.search(r"[\u4e00-\u9fff]", pre_text)
                    or re.search(r"[A-Z]{2,}", pre_text)
                ):
                    payee = pre_text[:60]

        # Fallback B: word-cluster scan — accepts short Chinese names or ALL-CAPS English brands
        if not payee:
            _clusters = re.findall(r"[\u4e00-\u9fff（）()A-Za-z0-9\-']{2,30}", t[:400])
            for cluster in _clusters:
                has_chinese = bool(re.search(r"[\u4e00-\u9fff]", cluster))
                has_brand = bool(re.search(r"[A-Z]{2,}", cluster))
                if (has_chinese or has_brand) and not _noise.search(cluster) and len(cluster) >= 2:
                    payee = cluster[:60]
                    break

    # ── Bank / Payment network ────────────────────────────────────────────────
    bank = ""
    for keyword in ("MASTERCARD", "VISA", "UNIONPAY", "ALIPAY", "PAYME", "OCTOPUS", "AMEX", "JCB", "KPAY", "WISPAY"):
        if keyword.lower() in t.lower():
            bank = keyword.title()
            break

    # ── Payer ─────────────────────────────────────────────────────────────────
    payer = ""
    m = re.search(
        r"(?:Cardholder|Card\s*holder|持卡人(?:姓名)?)[:\s]+([A-Z][A-Z /\-]{2,40}?)(?:\s+(?:COPY|RECEIPT|存根))?$",
        t, re.IGNORECASE | re.MULTILINE,
    )
    if m:
        candidate = m.group(1).strip().rstrip("/- ")
        if len(candidate) >= 4 and " " in candidate or "/" in candidate:
            payer = candidate

    # ── Short memo (first 80 chars of receipt) ────────────────────────────────
    memo_text = " ".join(t.split())[:80]

    # Require at least amount or date to consider this extraction valid.
    if not amount and not date:
        return None

    return {
        "voucher_no": voucher,
        "transaction_type": "AP",
        "amount": amount,
        "currency": currency,
        "date": date,
        "payer": payer,
        "payee": payee,
        "bank": bank,
        "category": "",
        "memo": memo_text,
        "confidence": "0.90" if (amount and date and payee) else "0.75" if (amount and date) else "0.50" if (amount or date) else "0.30",
    }


_AP_KNOWN_FIELDS = {
    "voucher_no", "transaction_type", "amount", "currency",
    "date", "payer", "payee", "bank", "category", "memo", "confidence",
    "needs_review", "validation_flags", "extraction_provenance",
    # Chinese aliases
    "金額", "日期", "付款人", "收款人", "銀行", "分類", "備註", "憑證號", "類型",
    # common model variants
    "total", "sales", "subtotal", "invoice_number", "receipt_no",
    "vendor", "supplier", "merchant", "store", "shop",
    "invoice_date", "receipt_date",
}


def _parse_ap_tsv_rows(raw_text: str) -> list[dict[str, str]]:
    """
    Parse the structured OCR output into AP transaction rows.
    Handles multiple formats the model may return:
      - TSV (tab-separated)
      - CSV (comma-separated)
      - Markdown / pipe table  (| col | col |, with optional separator row)
      - JSON object  {"amount": "...", ...}
      - JSON array   [{"amount": "..."}, ...]
      - Key-value text  amount: 158.00
    """
    import json
    import re

    if not raw_text or not raw_text.strip():
        return []

    # Strip common markdown code fences.
    text = re.sub(r"```[a-z]*", "", raw_text).replace("```", "").strip()

    # ── 1. JSON object or array ────────────────────────────────────────────────
    json_start = text.find("{")
    arr_start = text.find("[")
    if json_start >= 0 or arr_start >= 0:
        start = json_start if arr_start < 0 else (arr_start if json_start < 0 else min(json_start, arr_start))
        try:
            payload = json.loads(text[start:])
        except Exception:
            # Try extracting the first JSON object/array snippet.
            snippet = re.search(r"(\{[^{}]+\}|\[[^\[\]]+\])", text)
            try:
                payload = json.loads(snippet.group(0)) if snippet else None
            except Exception:
                payload = None
        if payload is not None:
            rows_raw: list[dict] = payload if isinstance(payload, list) else [payload]
            results = []
            for obj in rows_raw:
                if isinstance(obj, dict):
                    str_row = {str(k).lower().strip(): str(v).strip() for k, v in obj.items()}
                    norm = _normalize_ap_tsv_row_aliases(str_row)
                    if any(norm.values()):
                        results.append(norm)
            if results:
                return results

    # ── 2. Tabular formats: TSV / CSV / pipe-delimited ────────────────────────
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if len(lines) >= 2:
        # Detect delimiter from the first content line.
        header_line = lines[0]
        if "\t" in header_line:
            delim = "\t"
        elif "|" in header_line:
            delim = "|"
        elif "," in header_line:
            delim = ","
        else:
            delim = None

        if delim:
            raw_headers = [h.strip().lower().strip("|").strip() for h in header_line.split(delim) if h.strip().strip("|").strip()]
            # Accept if at least 2 known field names appear in the header.
            known_count = sum(1 for h in raw_headers if h in _AP_KNOWN_FIELDS)
            if known_count >= 2:
                parsed_rows = []
                for line in lines[1:]:
                    # Skip markdown separator rows like |---|---|
                    if re.match(r"^[\s|:\-]+$", line):
                        continue
                    parts = [p.strip().strip("|").strip() for p in line.split(delim)]
                    if len(parts) < 2:
                        continue
                    if len(parts) < len(raw_headers):
                        parts += [""] * (len(raw_headers) - len(parts))
                    raw_row = dict(zip(raw_headers, parts))
                    norm = _normalize_ap_tsv_row_aliases(raw_row)
                    if any(norm.values()):
                        parsed_rows.append(norm)
                if parsed_rows:
                    return parsed_rows

    # ── 3. Key-value text  (amount: 158.00  /  date = 2023-10-14) ─────────────
    kv: dict[str, str] = {}
    for line in lines:
        m = re.match(r"^([a-zA-Z_\u4e00-\u9fff]+)\s*[:=]\s*(.+)$", line)
        if m:
            kv[m.group(1).lower().strip()] = m.group(2).strip()
    if kv:
        norm = _normalize_ap_tsv_row_aliases(kv)
        if any(norm.values()):
            return [norm]

    return []


def _normalise_ap_row_with_regex(row: dict[str, str], ocr_text: str) -> dict[str, str]:
    """
    Post-process / normalise an AI-extracted AP row using regex.
    The AI provides the primary values; regex cleans and fills gaps only.
    Never overwrites a non-empty AI value with an empty regex result.
    """
    regex_row = _extract_ap_fields_from_text(ocr_text) or {}

    def _prefer_ai(key: str) -> str:
        ai_val = str(row.get(key) or "").strip()
        rx_val = str(regex_row.get(key) or "").strip()
        return ai_val if ai_val else rx_val

    # Amount: use AI value; clean currency symbols regardless of source
    amount_raw = _prefer_ai("amount")
    amount = _clean_amount(amount_raw) if amount_raw else ""

    # Date: normalise to YYYY-MM-DD regardless of what the AI returned
    date_raw = _prefer_ai("date")
    date = ""
    if date_raw:
        # Already YYYY-MM-DD?
        m = re.match(r"^(20\d{2})-(\d{2})-(\d{2})$", date_raw)
        if m:
            date = date_raw
        else:
            # Try DD/MM/YYYY
            m2 = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})$", date_raw)
            if m2:
                dd, mm, yyyy = int(m2.group(1)), int(m2.group(2)), m2.group(3)
                if 1 <= dd <= 31 and 1 <= mm <= 12:
                    date = f"{yyyy}-{mm:02d}-{dd:02d}"
            if not date:
                # Try YYYY/MM/DD
                m3 = re.match(r"^(20\d{2})[/\-](\d{1,2})[/\-](\d{1,2})$", date_raw)
                if m3:
                    date = f"{m3.group(1)}-{int(m3.group(2)):02d}-{int(m3.group(3)):02d}"

    # Currency: normalise HK$ → HKD, default HKD
    currency_raw = _prefer_ai("currency")
    currency = "HKD"
    if currency_raw:
        currency = currency_raw.upper().replace("HK$", "HKD").strip() or "HKD"

    return {
        "voucher_no":       _prefer_ai("voucher_no"),
        "transaction_type": "AP",
        "amount":           amount,
        "currency":         currency,
        "date":             date,
        "payer":            _prefer_ai("payer"),
        "payee":            _prefer_ai("payee"),
        "bank":             _prefer_ai("bank"),
        "category":         row.get("category", ""),
        "memo":             _prefer_ai("memo") or ocr_text[:120].strip(),
        "confidence":       str(row.get("confidence", "0.60")),
    }


def _extract_cheque_fields_from_text(ocr_text: str) -> dict[str, str] | None:
    """Heuristic regex for HK cheque pass-1 text; supports gap-fill and fallback row."""
    t = ocr_text.strip()
    if not t:
        return None
    amount = ""
    m = re.search(
        r"HK\$\s*([\d,]+\.?\d{0,2})|H\.?\s*K\.\s*DOLLARS?\s*([\d,]+\.?\d{0,2})|([\d,]+\.?\d{0,2})\s*HKD",
        t,
        re.IGNORECASE,
    )
    if m:
        g = m.group(1) or m.group(2) or m.group(3) or ""
        amount = _clean_amount(g) if g else ""
    chq = ""
    mq = re.search(
        r"[`'\"“”‘’]\s*([0-9]{6,8})\s*[`'\"“”‘’]|" r"(?:^|\D)([0-9]{6,8})(?:\D|$)",
        t.replace("\n", " "),
    )
    if mq:
        chq = (mq.group(1) or mq.group(2) or "").strip()
    if not amount and not chq and "祈付" not in t and "cheque" not in t.lower() and "支票" not in t:
        return None
    return {
        "voucher_no": chq,
        "amount": amount,
        "currency": "HKD",
        "date": "",
        "payer": "",
        "payee": "",
        "bank": "",
        "memo": t[:100].strip(),
        "confidence": "0.55",
    }


def _normalise_cheque_row_with_regex(
    row: dict[str, str],
    ocr_text: str,
    processing_mode: str,
) -> dict[str, str]:
    """Normalise a cheque VLM/regex row; regex fills gaps only."""
    rx_row = _extract_cheque_fields_from_text(ocr_text) or {}

    def _prefer_ai(key: str) -> str:
        ai_val = str(row.get(key) or "").strip()
        rx_val = str(rx_row.get(key) or "").strip()
        return ai_val if ai_val else rx_val

    amount_raw = _prefer_ai("amount")
    amount = _clean_amount(amount_raw) if amount_raw else ""
    date_raw = _prefer_ai("date")
    date = ""
    if date_raw:
        m = re.match(r"^(20\d{2})-(\d{2})-(\d{2})$", date_raw)
        if m:
            date = date_raw
        else:
            m2 = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](20\d{2})$", date_raw)
            if m2:
                dd, mm, yyyy = int(m2.group(1)), int(m2.group(2)), m2.group(3)
                if 1 <= dd <= 31 and 1 <= mm <= 12:
                    date = f"{yyyy}-{mm:02d}-{dd:02d}"
            if not date:
                m3 = re.match(r"^(20\d{2})[/\-](\d{1,2})[/\-](\d{1,2})$", date_raw)
                if m3:
                    date = f"{m3.group(1)}-{int(m3.group(2)):02d}-{int(m3.group(3)):02d}"
    cur_raw = _prefer_ai("currency")
    currency = "HKD"
    if cur_raw:
        currency = cur_raw.upper().replace("HK$", "HKD").strip() or "HKD"
    memo = _prefer_ai("memo")
    if not memo:
        memo = ocr_text[:120].strip()
    voucher_no = _prefer_ai("voucher_no")
    if voucher_no and "cheque_number=" not in memo:
        memo = " | ".join(x for x in (memo, f"cheque_number={voucher_no}") if x)
    return {
        "voucher_no": voucher_no,
        "transaction_type": processing_mode,
        "amount": amount,
        "currency": currency,
        "date": date,
        "payer": _prefer_ai("payer"),
        "payee": _prefer_ai("payee"),
        "bank": _prefer_ai("bank"),
        "category": row.get("category", "") or "",
        "memo": memo,
        "confidence": str(row.get("confidence", "0.60")),
    }


async def _extract_ap_ai_fields_for_page(
    ocr_text: str,
    img_path: str,
    page_num: int,
    ocr_provider_name: str,
    ocr_model_override: str,
    processing_mode: str = "AP",
    image_options: dict | None = None,
    ocr_lines: list[Any] | None = None,
    *,
    cross_verify: bool = False,
    rescan_supplement: str | None = None,
) -> dict:
    """
    AI-first structured field extraction for a single receipt/invoice page.
    Used for both AP and AR modes (processing_mode controls transaction_type).

    Strategy:
      1. Call the structured OCR model with a JSON prompt (temperature=0.0).
      2. Parse the JSON response and normalise fields with regex.
      3. If the AI call fails or returns no parseable JSON, fall back to
         regex-only extraction on the Pass-1 OCR text.
      4. Bare fallback row as last resort.
    """
    # Sanitise OCR text before injecting into AI prompt (indirect injection guard)
    ocr_text = sanitise_ocr_text(ocr_text)
    model = ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL
    extraction_source = "none"
    tsv_rows: list[dict[str, Any]] = []
    receipt_json_obj: dict | None = None

    layout_compact = _extraction_validation.format_ocr_layout_hint_from_lines(ocr_lines or [])

    # ── Primary: structured OCR with JSON prompt (temperature=0.0) ────────────
    try:
        structured_prompt = _build_ap_multi_receipt_structured_prompt(
            ocr_text_hint=ocr_text,
            layout_hint=layout_compact,
            cross_verify=cross_verify,
            rescan_supplement=rescan_supplement,
        )
        structured_result = await _ocr_service.recognize(
            img_path,
            provider_name=ocr_provider_name,
            model=model,
            prompt_override=structured_prompt,
            ocr_options={"temperature": 0.0},
            image_options=image_options,
        )
        raw_text = structured_result.text.strip()
        logger.info(
            "   [AP page %s] AI-primary raw (first 400 chars): %s",
            page_num, raw_text[:400].replace("\n", "↵"),
        )

        # Try to parse JSON from the model response
        json_obj = None
        # Strip markdown code fences if present
        json_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE).strip()
        try:
            json_obj = json.loads(json_text)
        except (json.JSONDecodeError, ValueError):
            # Try to extract first {...} block
            m = re.search(r"\{.*\}", json_text, re.DOTALL)
            if m:
                try:
                    json_obj = json.loads(m.group(0))
                except (json.JSONDecodeError, ValueError):
                    pass

        if json_obj and isinstance(json_obj, dict):
            # Map JSON fields to internal TSV row format
            _cur_raw = json_obj.get("currency")
            if cross_verify:
                _cs = "" if _cur_raw is None else str(_cur_raw).strip()
            else:
                _cs = str(_cur_raw or "HKD").strip() or "HKD"
            raw_row = {
                "voucher_no":       str(json_obj.get("receipt_id") or ""),
                "transaction_type": processing_mode,
                "amount":           str(json_obj.get("total_amount") or ""),
                "currency":         _cs,
                "date":             str(json_obj.get("transaction_date") or ""),
                "payer":            "",
                "payee":            str(json_obj.get("merchant_name") or ""),
                "bank":             str(json_obj.get("payment_method") or ""),
                "category":         "",
                "memo":             "",
                "confidence":       str(json_obj.get("confidence") or "0.60"),
            }
            # Normalise with regex as post-processor
            norm_row = _normalise_ap_row_with_regex(raw_row, ocr_text)
            tsv_rows = [norm_row]
            extraction_source = "ai_json"
            receipt_json_obj = json_obj
            logger.info(
                "   [AP page %s] AI-JSON OK: amount=%s date=%s payee=%s conf=%s",
                page_num,
                norm_row.get("amount"), norm_row.get("date"),
                norm_row.get("payee"), norm_row.get("confidence"),
            )
        else:
            # JSON parse failed — try the existing TSV parser as secondary attempt
            tsv_rows = _parse_ap_tsv_rows(raw_text)
            if tsv_rows:
                extraction_source = "ai_tsv_fallback"
                logger.info("   [AP page %s] AI returned TSV (not JSON), parsed %d row(s).", page_num, len(tsv_rows))

    except Exception as exc:
        logger.warning("   [AP page %s] AI-primary call failed: %s", page_num, exc)

    # ── Secondary: regex on Pass-1 OCR text (if AI gave nothing usable) ───────
    if not tsv_rows:
        logger.info("   [AP page %s] AI gave no result; using regex on OCR text.", page_num)
        text_row = _extract_ap_fields_from_text(ocr_text)
        if text_row:
            tsv_rows = [text_row]
            extraction_source = "text_regex"
            logger.info(
                "   [AP page %s] regex OK: amount=%s date=%s payee=%s",
                page_num, text_row.get("amount"), text_row.get("date"), text_row.get("payee"),
            )

    # ── Last resort: bare fallback row ────────────────────────────────────────
    if not tsv_rows:
        logger.warning("   [AP page %s] all methods failed; bare fallback.", page_num)
        tsv_rows = [{
            "voucher_no": "", "transaction_type": processing_mode, "amount": "",
            "currency": "HKD", "date": "", "payer": "", "payee": "",
            "bank": "", "category": "",
            "memo": ocr_text[:120].strip(), "confidence": "0.30",
        }]
        extraction_source = "fallback"

    logger.info(
        "   [%s page %s] extraction_source=%s rows=%s",
        processing_mode, page_num, extraction_source, len(tsv_rows),
    )
    xcheck_text: str | None = None
    xcheck_reader = _ocr_cross_check.get_cross_check_reader()
    if xcheck_reader is not None and tsv_rows:
        try:
            xcheck_text = await asyncio.to_thread(xcheck_reader.read_text, img_path)
        except Exception as exc:
            logger.warning("   [AP page %s] OCR cross-check read failed: %s", page_num, exc)
            xcheck_text = None
    for ix, row in enumerate(tsv_rows):
        jr = receipt_json_obj if (extraction_source == "ai_json" and ix == 0) else None
        vr = _extraction_validation.validate_ar_ap_receipt(jr, row)
        _extraction_validation.merge_validation_into_row(row, vr)
        if xcheck_text:
            xr = _ocr_cross_check.cross_check_fields(xcheck_text, row)
            _extraction_validation.merge_validation_into_row(row, xr)
        if ix == 0:
            _extraction_validation.attach_amount_disambiguation(row, jr, ocr_text)
    out: dict[str, Any] = {
        "output_format": "tsv",
        "tsv_rows": tsv_rows,
        "ai_processed": True,
        "confidence": tsv_rows[0].get("confidence") if tsv_rows else "0.30",
        "extraction_source": extraction_source,
        "is_fallback": extraction_source in ("text_regex", "fallback", "ai_tsv_fallback"),
    }
    if tsv_rows:
        try:
            from decimal import Decimal

            from app.services.fx_reference import hkd_reference_fields

            amt0 = tsv_rows[0].get("amount")
            cur0 = str(tsv_rows[0].get("currency") or "HKD")
            d = Decimal(str(amt0).replace(",", "")) if amt0 not in (None, "") else None
            fx = hkd_reference_fields(d, cur0)
            if fx:
                out["fx_reference"] = fx
        except Exception:
            pass
    return out


async def _extract_cheque_deposit_advice_fields_for_page(
    ocr_text: str,
    img_path: str,
    page_num: int,
    ocr_provider_name: str,
    ocr_model_override: str,
    processing_mode: str = "AP",
    image_options: dict | None = None,
    *,
    rescan_supplement: str | None = None,
) -> dict:
    """
    VLM JSON extraction for cheque deposit advice / bank-in slips.
    Maps the requested slip fields into the existing TSV row contract.
    """
    ocr_text = sanitise_ocr_text(ocr_text)
    model = ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL
    extraction_source = "cheque_deposit_advice_vlm_json"
    tsv_rows: list[dict[str, Any]] = []

    try:
        structured_prompt = _build_cheque_deposit_advice_structured_prompt(
            ocr_text_hint=ocr_text,
            rescan_supplement=rescan_supplement,
        )
        structured_result = await _ocr_service.recognize(
            img_path,
            provider_name=ocr_provider_name,
            model=model,
            prompt_override=structured_prompt,
            ocr_options={"temperature": 0.0},
            image_options=image_options,
        )
        raw_text = structured_result.text.strip()
        logger.info(
            "   [DepositAdvice page %s] VLM raw (first 400 chars): %s",
            page_num, raw_text[:400].replace("\n", "↵"),
        )
        json_obj = None
        json_text = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE
        ).strip()
        try:
            json_obj = json.loads(json_text)
        except (json.JSONDecodeError, ValueError):
            m = re.search(r"\{.*\}", json_text, re.DOTALL)
            if m:
                try:
                    json_obj = json.loads(m.group(0))
                except (json.JSONDecodeError, ValueError):
                    pass

        if json_obj and isinstance(json_obj, dict):
            amount = _clean_amount(str(json_obj.get("amount") or ""))
            date_time = str(json_obj.get("date_time") or "").strip()
            date = ""
            m_date = re.match(r"^(20\d{2}-\d{2}-\d{2})", date_time)
            if m_date:
                date = m_date.group(1)
            cheque_number = str(json_obj.get("cheque_number") or "").strip()
            reference = str(json_obj.get("reference") or "").strip()
            terminal_no = str(json_obj.get("terminal_no") or "").strip()
            account_no = str(json_obj.get("payee_account_number") or "").strip()
            account_name = str(json_obj.get("payee_account_name") or "").strip()
            memo_parts = [
                "document_type=cheque_deposit_advice",
                f"account_no={account_no}" if account_no else "",
                f"date_time={date_time}" if date_time else "",
                f"cheque_count={json_obj.get('cheque_count')}" if json_obj.get("cheque_count") is not None else "",
                f"terminal_no={terminal_no}" if terminal_no else "",
                f"reference={reference}" if reference else "",
            ]
            tsv_rows = [{
                "voucher_no": cheque_number or reference or terminal_no,
                "transaction_type": processing_mode,
                "amount": amount,
                "currency": str(json_obj.get("currency") or "HKD").upper().replace("HK$", "HKD"),
                "date": date,
                "payer": "",
                "payee": account_name or account_no,
                "bank": str(json_obj.get("bank_name") or "").strip(),
                "category": "",
                "memo": " | ".join(p for p in memo_parts if p),
                "confidence": str(json_obj.get("confidence") or "0.70"),
            }]
            logger.info(
                "   [DepositAdvice page %s] JSON OK: account=%s amount=%s date_time=%s",
                page_num, account_no, amount, date_time,
            )
    except Exception as exc:
        logger.warning("   [DepositAdvice page %s] VLM structured failed: %s", page_num, exc)

    if not tsv_rows:
        logger.warning("   [DepositAdvice page %s] extraction fallback.", page_num)
        tsv_rows = [{
            "voucher_no": "", "transaction_type": processing_mode, "amount": "",
            "currency": "HKD", "date": "", "payer": "", "payee": "",
            "bank": "", "category": "",
            "memo": ocr_text[:120].strip(), "confidence": "0.30",
        }]
        extraction_source = "cheque_deposit_advice_fallback"

    logger.info(
        "   [DepositAdvice %s page %s] extraction_source=%s rows=%s",
        processing_mode, page_num, extraction_source, len(tsv_rows),
    )
    for row in tsv_rows:
        vr = _extraction_validation.validate_deposit_advice_row(None, row)
        _extraction_validation.merge_validation_into_row(row, vr)
    return {
        "output_format": "tsv",
        "tsv_rows": tsv_rows,
        "ai_processed": True,
        "confidence": tsv_rows[0].get("confidence") if tsv_rows else "0.30",
        "extraction_source": extraction_source,
    }


async def _extract_cheque_fields_for_page(
    ocr_text: str,
    img_path: str,
    page_num: int,
    ocr_provider_name: str,
    ocr_model_override: str,
    processing_mode: str = "AP",
    image_options: dict | None = None,
    router_orientation_degrees: int | None = None,
    router_ocr_text_hint: str | None = None,
    *,
    rescan_supplement: str | None = None,
) -> dict:
    """
    VLM JSON extraction for Hong Kong cheques; maps to the same tsv_rows contract (voucher_no = chq#).
    """
    ocr_text = sanitise_ocr_text(ocr_text)
    router_hint = sanitise_ocr_text(router_ocr_text_hint or "")
    if router_hint:
        ocr_text = router_hint
    orient_cleanup: list[str] = []
    resolved_orientation_degrees: int | None = None
    try:
        if router_orientation_degrees is not None and CHEQUE_AUTO_ROTATE_ENABLED:
            deg = router_orientation_degrees % 360
            resolved_orientation_degrees = deg
            if deg == 0:
                logger.info(
                    "[Chq page %s] reusing router orientation: %s deg text_len=%s",
                    page_num, deg, len(ocr_text),
                )
            else:
                oriented_path = _build_cheque_orientation_variant(img_path, deg)
                img_path = oriented_path
                orient_cleanup = [oriented_path]
                logger.info(
                    "[Chq page %s] reusing router orientation: %s deg text_len=%s",
                    page_num, deg, len(ocr_text),
                )
        else:
            img_path, ocr_text, orient_cleanup, probe_deg = await _pick_best_cheque_image_orientation(
                img_path,
                ocr_text,
                ocr_provider_name,
                ocr_model_override,
                page_num,
            )
            if probe_deg is not None:
                resolved_orientation_degrees = probe_deg
        ocr_text = sanitise_ocr_text(ocr_text)
    except Exception as exc:
        logger.warning("[Chq page %s] orientation probe skipped: %s", page_num, exc)

    try:
        model = ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL
        extraction_source = "none"
        tsv_rows: list[dict[str, Any]] = []
        cheque_json_obj: dict | None = None

        try:
            structured_prompt = _build_cheque_structured_prompt(
                ocr_text_hint=ocr_text,
                rescan_supplement=rescan_supplement,
            )
            structured_result = await _ocr_service.recognize(
                img_path,
                provider_name=ocr_provider_name,
                model=model,
                prompt_override=structured_prompt,
                ocr_options={"temperature": 0.0},
                image_options=image_options,
            )
            raw_text = structured_result.text.strip()
            logger.info(
                "   [Chq page %s] VLM raw (first 400 chars): %s",
                page_num, raw_text[:400].replace("\n", "↵"),
            )
            json_obj = None
            json_text = re.sub(
                r"^```(?:json)?\s*|\s*```$", "", raw_text, flags=re.MULTILINE
            ).strip()
            try:
                json_obj = json.loads(json_text)
            except (json.JSONDecodeError, ValueError):
                m = re.search(r"\{.*\}", json_text, re.DOTALL)
                if m:
                    try:
                        json_obj = json.loads(m.group(0))
                    except (json.JSONDecodeError, ValueError):
                        pass
            if json_obj and isinstance(json_obj, dict):
                am = json_obj.get("amount_numeric")
                if am is not None and not isinstance(am, (str, int, float)):
                    am = str(am)
                aw = str(json_obj.get("amount_words") or "").strip()
                me = str(json_obj.get("memo") or "").strip()
                memo = " | ".join(x for x in (aw, me) if x) if (aw or me) else ""
                raw_row = {
                    "voucher_no": str(json_obj.get("cheque_number") or "").strip(),
                    "amount": str(am or ""),
                    "currency": str(json_obj.get("currency") or "HKD"),
                    "date": str(json_obj.get("date") or "").strip(),
                    "payer": str(json_obj.get("payer") or "").strip(),
                    "payee": str(json_obj.get("payee") or "").strip(),
                    "bank": str(json_obj.get("bank_name") or "").strip(),
                    "category": "",
                    "memo": memo,
                    "confidence": str(json_obj.get("confidence") or "0.60"),
                }
                norm_row = _normalise_cheque_row_with_regex(
                    raw_row, ocr_text, processing_mode
                )
                tsv_rows = [norm_row]
                extraction_source = "cheque_vlm_json"
                cheque_json_obj = json_obj
                logger.info(
                    "   [Chq page %s] JSON OK: chq#=%s amount=%s date=%s",
                    page_num,
                    norm_row.get("voucher_no"),
                    norm_row.get("amount"),
                    norm_row.get("date"),
                )
            else:
                tsv_try = _parse_ap_tsv_rows(raw_text)
                if tsv_try:
                    fixed: list[dict[str, str]] = []
                    for r in tsv_try:
                        fixed.append(
                            _normalise_cheque_row_with_regex(
                                {str(k): str(v) for k, v in r.items()},
                                ocr_text,
                                processing_mode,
                            )
                        )
                    tsv_rows = fixed
                    extraction_source = "cheque_tsv_fallback"
        except Exception as exc:
            logger.warning("   [Chq page %s] VLM structured failed: %s", page_num, exc)

        if not tsv_rows:
            tr = _extract_cheque_fields_from_text(ocr_text)
            if tr:
                norm = _normalise_cheque_row_with_regex(
                    {**tr, "category": ""},
                    ocr_text,
                    processing_mode,
                )
                tsv_rows = [norm]
                extraction_source = "cheque_text_regex"
                logger.info(
                    "   [Chq page %s] regex: amount=%s chq#=%s",
                    page_num, norm.get("amount"), norm.get("voucher_no"),
                )
        if not tsv_rows:
            logger.warning("   [Chq page %s] cheque extraction fallback.", page_num)
            tsv_rows = [{
                "voucher_no": "", "transaction_type": processing_mode, "amount": "",
                "currency": "HKD", "date": "", "payer": "", "payee": "",
                "bank": "", "category": "",
                "memo": ocr_text[:120].strip(), "confidence": "0.30",
            }]
            extraction_source = "cheque_fallback"

        logger.info(
            "   [Chq %s page %s] extraction_source=%s rows=%s",
            processing_mode, page_num, extraction_source, len(tsv_rows),
        )
        for ix, row in enumerate(tsv_rows):
            jc = cheque_json_obj if extraction_source == "cheque_vlm_json" and ix == 0 else None
            vr = _extraction_validation.validate_cheque_row(jc, row)
            _extraction_validation.merge_validation_into_row(row, vr)
        result: dict[str, Any] = {
            "output_format": "tsv",
            "tsv_rows": tsv_rows,
            "ai_processed": True,
            "confidence": tsv_rows[0].get("confidence") if tsv_rows else "0.30",
            "extraction_source": extraction_source,
        }
        if resolved_orientation_degrees is not None:
            # Consumed (and removed) by _ap_apply_cross_vlm_merge_if_configured so the
            # cross pass does not repeat the 4-way orientation probe.
            result["cheque_orientation"] = {
                "degrees": resolved_orientation_degrees,
                "text": ocr_text,
            }
        return result
    finally:
        for p in orient_cleanup:
            try:
                if p and os.path.isfile(p):
                    os.unlink(p)
            except OSError:
                pass


async def _extract_ar_ap_ai_fields_routed(
    ocr_text: str,
    img_path: str,
    page_num: int,
    ocr_provider_name: str,
    ocr_model_override: str,
    processing_mode: str,
    image_options: dict | None = None,
    cheque_probe: dict[str, Any] | None = None,
    ocr_lines: list[Any] | None = None,
    *,
    cross_verify: bool = False,
    rescan_supplement: str | None = None,
) -> dict:
    cheque_probe = cheque_probe if isinstance(cheque_probe, dict) else None
    probe_matched = bool(cheque_probe and cheque_probe.get("matched"))
    probe_text = sanitise_ocr_text(str(cheque_probe.get("text") or "")) if cheque_probe else ""
    probe_degrees = cheque_probe.get("degrees") if cheque_probe else None
    if not isinstance(probe_degrees, int):
        probe_degrees = None

    if processing_mode in ("AR", "AP") and _is_cheque_deposit_advice(ocr_text):
        return await _extract_cheque_deposit_advice_fields_for_page(
            ocr_text=ocr_text,
            img_path=img_path,
            page_num=page_num,
            ocr_provider_name=ocr_provider_name,
            ocr_model_override=ocr_model_override,
            processing_mode=processing_mode,
            image_options=image_options,
            rescan_supplement=rescan_supplement,
        )

    if processing_mode in ("AR", "AP") and (probe_matched or _is_cheque_document(ocr_text)):
        return await _extract_cheque_fields_for_page(
            ocr_text=probe_text or ocr_text,
            img_path=img_path,
            page_num=page_num,
            ocr_provider_name=ocr_provider_name,
            ocr_model_override=ocr_model_override,
            processing_mode=processing_mode,
            image_options=image_options,
            router_orientation_degrees=probe_degrees if probe_matched else None,
            router_ocr_text_hint=probe_text if probe_matched else None,
            rescan_supplement=rescan_supplement,
        )
    return await _extract_ap_ai_fields_for_page(
        ocr_text=ocr_text,
        img_path=img_path,
        page_num=page_num,
        ocr_provider_name=ocr_provider_name,
        ocr_model_override=ocr_model_override,
        processing_mode=processing_mode,
        image_options=image_options,
        ocr_lines=ocr_lines,
        cross_verify=cross_verify,
        rescan_supplement=rescan_supplement,
    )


async def _ap_apply_cross_vlm_merge_if_configured(
    *,
    processing_mode: str,
    primary_model: str,
    ai_primary: dict[str, Any],
    ocr_text: str,
    img_path: str,
    page_num: int,
    ocr_provider_name: str,
    image_options: dict[str, Any] | None,
    ocr_lines: list[Any] | None,
    cheque_probe: dict[str, Any] | None = None,
    rescan_supplement: str | None = None,
) -> dict[str, Any]:
    """Second structured pass with configured cross VLM; merge into primary `ai_enhanced` dict."""
    # Always strip the orientation hint so it never leaks into stored payloads.
    cheque_orientation = (
        ai_primary.pop("cheque_orientation", None) if isinstance(ai_primary, dict) else None
    )
    if processing_mode != "AP" or not isinstance(ai_primary, dict):
        return ai_primary
    cross_model = (settings.ap_cross_vlm_model or "").strip()
    if not cross_model:
        return ai_primary
    force = _ap_cross_verify_force_cv.get()
    if not (force or settings.ap_auto_cross_verify_enabled):
        return ai_primary
    pm = (primary_model or "").strip()
    if pm.lower() == cross_model.lower():
        return ai_primary
    if not force:
        skip_thresh = float(settings.ap_auto_cross_verify_skip_primary_confidence or 0.0)
        if skip_thresh > 0.0:
            primary_min = _min_tsv_confidence(ai_primary)
            if primary_min is not None and primary_min >= skip_thresh:
                logger.info(
                    "[AP cross-VLM] skipped: primary min confidence %.2f >= %.2f (page=%s)",
                    primary_min,
                    skip_thresh,
                    page_num,
                )
                return ai_primary
    if (
        not (isinstance(cheque_probe, dict) and cheque_probe.get("matched"))
        and isinstance(cheque_orientation, dict)
        and isinstance(cheque_orientation.get("degrees"), int)
    ):
        # Reuse orientation resolved by the primary cheque pass so the cross pass
        # skips its own 4-way orientation probe.
        cheque_probe = {
            "matched": True,
            "text": str(cheque_orientation.get("text") or ""),
            "degrees": cheque_orientation["degrees"],
            "score": 0.0,
        }
    timeout_s = max(1.0, settings.ap_auto_cross_verify_timeout_ms / 1000.0)
    try:
        cross_ai = await asyncio.wait_for(
            _extract_ar_ap_ai_fields_routed(
                ocr_text=ocr_text,
                img_path=img_path,
                page_num=page_num,
                ocr_provider_name=ocr_provider_name,
                ocr_model_override=cross_model,
                processing_mode=processing_mode,
                image_options=image_options,
                cheque_probe=cheque_probe,
                ocr_lines=ocr_lines,
                cross_verify=True,
                rescan_supplement=rescan_supplement,
            ),
            timeout=timeout_s,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "[AP cross-VLM] timed out after %ss; keeping primary (page=%s)",
            timeout_s,
            page_num,
        )
        return ai_primary
    except OcrBackgroundJobCancelled:
        raise
    except Exception as exc:
        logger.warning(
            "[AP cross-VLM] second pass failed (page=%s): %s",
            page_num,
            exc,
        )
        return ai_primary
    if not isinstance(cross_ai, dict):
        return ai_primary
    thresh = float(settings.ap_auto_cross_verify_confidence_threshold or 0.0)
    if not cross_extraction_passes_confidence_gate(cross_ai, thresh):
        return ai_primary
    policy = settings.ap_auto_cross_verify_policy or "aggressive_overwrite"
    return merge_ap_ai_enhanced_primary_with_cross(
        ai_primary,
        cross_ai,
        cross_model=cross_model,
        policy=policy,
    )


def _parse_json_object_from_vlm_layout(raw_text: str) -> dict | None:
    """Strip fences and parse first JSON object from VLM layout response."""
    if not raw_text or not raw_text.strip():
        return None
    json_text = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_text.strip(), flags=re.MULTILINE).strip()
    try:
        obj = json.loads(json_text)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        m = re.search(r"\{.*\}", json_text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                return obj if isinstance(obj, dict) else None
            except (json.JSONDecodeError, ValueError):
                pass
    return None


def _write_ap_layout_thumbnail(image_path: str, max_side: int) -> tuple[str, int, int, int, int]:
    """
    Resize page image so longest side <= max_side; write temp JPEG.
    Returns (temp_path, thumb_w, thumb_h, full_w, full_h).
    """
    from PIL import Image

    with Image.open(image_path) as img:
        full_w, full_h = img.size
        img_rgb = img.convert("RGB")
        scale = min(max_side / max(full_w, full_h), 1.0)
        tw = max(1, int(round(full_w * scale)))
        th = max(1, int(round(full_h * scale)))
        thumb = img_rgb.resize((tw, th), Image.LANCZOS)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ap-layout-thumb.jpg")
        tmp_path = tmp.name
        tmp.close()
        thumb.save(tmp_path, format="JPEG", quality=88)
    return tmp_path, tw, th, full_w, full_h


def _layout_boxes_to_pixel_regions(
    receipts: list,
    full_w: int,
    full_h: int,
    pad_pct: float,
) -> list[dict[str, int]]:
    """
    Normalized 0-1 boxes (x,y,w,h) -> integer pixel dicts for _crop_receipt_regions.
    Applies symmetric inset pad_pct of each box's w and h, then clamps to image bounds.
    """
    _EPS = 1e-6
    regions: list[dict[str, int]] = []
    pad = max(0.0, min(pad_pct, 0.45))

    for raw in receipts:
        if not isinstance(raw, dict):
            continue
        try:
            x = float(raw.get("x", 0))
            y = float(raw.get("y", 0))
            w = float(raw.get("w", 0))
            h = float(raw.get("h", 0))
        except (TypeError, ValueError):
            continue
        if w <= _EPS or h <= _EPS:
            continue
        # Inset: shrink box by pad_pct of its own width/height on each side
        x2 = x + pad * w
        y2 = y + pad * h
        w2 = w * (1.0 - 2.0 * pad)
        h2 = h * (1.0 - 2.0 * pad)
        if w2 <= _EPS or h2 <= _EPS:
            continue
        x2 = max(0.0, min(x2, 1.0 - _EPS))
        y2 = max(0.0, min(y2, 1.0 - _EPS))
        w2 = min(w2, 1.0 - x2)
        h2 = min(h2, 1.0 - y2)
        if w2 < 0.01 or h2 < 0.01:
            continue

        rx = int(x2 * full_w)
        ry = int(y2 * full_h)
        rw = max(1, int(round(w2 * full_w)))
        rh = max(1, int(round(h2 * full_h)))
        rx = max(0, min(rx, full_w - 1))
        ry = max(0, min(ry, full_h - 1))
        rw = min(rw, full_w - rx)
        rh = min(rh, full_h - ry)
        if rw < 1 or rh < 1:
            continue
        regions.append({"x": rx, "y": ry, "w": rw, "h": rh})

    regions.sort(key=lambda r: (r["y"] // 200, r["x"]))
    return regions


def _validate_layout_json(obj: dict) -> tuple[bool, str]:
    """Return (ok, reason)."""
    try:
        conf = float(obj.get("confidence", 0))
    except (TypeError, ValueError):
        return False, "invalid_confidence"
    if conf < AP_VLM_LAYOUT_CONFIDENCE_MIN:
        return False, "low_confidence"

    receipts = obj.get("receipts")
    if not isinstance(receipts, list):
        return False, "missing_receipts"
    if len(receipts) < 2:
        return False, "fewer_than_two_regions"

    try:
        declared = int(obj.get("count", len(receipts)))
    except (TypeError, ValueError):
        declared = len(receipts)
    if declared > len(receipts):
        return False, "count_exceeds_boxes"

    _EPS = 1e-4
    _MIN_FRAC = 0.01
    for raw in receipts:
        if not isinstance(raw, dict):
            return False, "invalid_box_shape"
        try:
            x = float(raw["x"])
            y = float(raw["y"])
            w = float(raw["w"])
            h = float(raw["h"])
        except (KeyError, TypeError, ValueError):
            return False, "invalid_box_fields"
        if x < -_EPS or y < -_EPS or w <= _EPS or h <= _EPS:
            return False, "box_out_of_range"
        if x + w > 1.0 + _EPS or y + h > 1.0 + _EPS:
            return False, "box_overflow"
        if w < _MIN_FRAC or h < _MIN_FRAC:
            return False, "box_too_small"

    return True, "ok"


def _layout_count_matches_receipts(obj: dict) -> bool:
    """True when absent count or count equals len(receipts); used to decide layout retry."""
    receipts = obj.get("receipts")
    if not isinstance(receipts, list):
        return True
    if "count" not in obj:
        return True
    try:
        declared = int(obj["count"])
    except (TypeError, ValueError):
        return True
    return declared == len(receipts)


async def _ap_vlm_layout_try_receipt_regions(
    image_path: str,
    *,
    ocr_provider_name: str,
    ocr_model_override: str,
    background_job_id: str | None = None,
    rescan_supplement: str | None = None,
    expected_receipt_count: int | None = None,
    vlm_only: bool = False,
) -> list[dict[str, int]] | None:
    """
    Settings VLM layout: thumbnail + JSON boxes. Returns pixel regions or None.
    When vlm_only=True: one Detect call, multi-schema parse, no OpenCV fallback.
    Model is Settings VLM (ocr_model_override); empty model → no Detect.
    """
    thumb_path: str | None = None
    max_attempts = 1 if vlm_only else (1 + AP_VLM_LAYOUT_MAX_RETRIES)
    expected = normalize_expected_receipt_count(expected_receipt_count)
    try:
        thumb_path, _tw, _th, full_w, full_h = _write_ap_layout_thumbnail(
            image_path, AP_VLM_LAYOUT_THUMB_MAX_SIDE,
        )
        model = (ocr_model_override or "").strip()
        if not model:
            logger.warning("[AP layout] Settings VLM unset; skipping Detect.")
            return None

        obj: dict | None = None
        for attempt in range(max_attempts):
            _raise_if_bg_job_cancelled(background_job_id)
            use_repair = (not vlm_only) and attempt > 0
            if vlm_only:
                prompt = VLM_RECEIPT_DETECT_PROMPT
            elif use_repair:
                prompt = AP_VLM_LAYOUT_DETECTION_PROMPT_REPAIR
            else:
                prompt = AP_VLM_LAYOUT_DETECTION_PROMPT
            layout_hints: list[str] = []
            if expected is not None:
                if vlm_only:
                    layout_hints.append(
                        f"User-stated physical receipt count for this page: {expected}. "
                        f"Return exactly {expected} objects (one box per distinct slip). "
                        "Do not invent empty slips."
                    )
                else:
                    layout_hints.append(
                        f"User-stated physical receipt count for this page: {expected}. "
                        f'Set "count" to {expected} and return exactly {expected} boxes in '
                        '"receipts" (one box per distinct slip). Do not invent empty slips.'
                    )
            sup = (rescan_supplement or "").strip()
            if sup:
                layout_hints.append(sup)
            if layout_hints:
                prompt = prompt + "\n\n" + "\n".join(layout_hints)
            logger.info(
                "[AP layout] attempt=%s/%s prompt=%s max_retries_env=%s expected_count=%s vlm_only=%s",
                attempt + 1,
                max_attempts,
                "vlm_detect" if vlm_only else ("repair" if use_repair else "initial"),
                AP_VLM_LAYOUT_MAX_RETRIES,
                expected,
                vlm_only,
            )
            ocr_options: dict[str, Any] = {"temperature": 0.0}
            if vlm_only:
                ocr_options["max_tokens"] = 1024
            result = await _ocr_service.recognize(
                thumb_path,
                provider_name=ocr_provider_name,
                model=model,
                prompt_override=prompt,
                ocr_options=ocr_options,
                image_options={
                    "max_side": 0,
                    "format": "JPEG",
                    "quality": AP_VLM_LAYOUT_JPEG_QUALITY,
                },
            )
            raw_text = (result.text or "").strip()
            if vlm_only:
                regions = parse_vlm_detect_regions(
                    raw_text,
                    full_w=full_w,
                    full_h=full_h,
                    pad_pct=AP_VLM_LAYOUT_BOX_PAD_PCT,
                )
                if not regions:
                    logger.warning(
                        "[AP layout] Settings VLM Detect returned no usable boxes (attempt %s/%s).",
                        attempt + 1,
                        max_attempts,
                    )
                    return None
                logger.info(
                    "[AP layout] Using Settings VLM Detect boxes: regions=%s opencv_calls=0",
                    len(regions),
                )
                return regions

            obj = _parse_json_object_from_vlm_layout(raw_text)
            if not obj:
                logger.warning(
                    "[AP layout] VLM returned no parseable JSON (attempt %s/%s).",
                    attempt + 1,
                    max_attempts,
                )
                if attempt < max_attempts - 1:
                    continue
                logger.warning("[AP layout] Giving up; falling back to OpenCV.")
                return None

            ok, reason = _validate_layout_json(obj)
            if not ok:
                logger.warning(
                    "[AP layout] Validation failed (%s) conf=%s (attempt %s/%s).",
                    reason,
                    obj.get("confidence"),
                    attempt + 1,
                    max_attempts,
                )
                if reason in AP_VLM_LAYOUT_EARLY_FALLBACK_REASONS:
                    logger.warning(
                        "[AP layout] Early fallback triggered by reason=%s (attempt %s/%s).",
                        reason,
                        attempt + 1,
                        max_attempts,
                    )
                    return None
                if attempt < max_attempts - 1:
                    continue
                logger.warning("[AP layout] Falling back to OpenCV after validation failures.")
                return None

            if not _layout_count_matches_receipts(obj):
                receipts = obj.get("receipts")
                rlen = len(receipts) if isinstance(receipts, list) else 0
                try:
                    cval = int(obj.get("count", rlen))
                except (TypeError, ValueError):
                    cval = rlen
                logger.warning(
                    "[AP layout] count vs receipts mismatch: count=%s len(receipts)=%s (attempt %s/%s).",
                    cval,
                    rlen,
                    attempt + 1,
                    max_attempts,
                )
                if attempt < max_attempts - 1:
                    continue
                logger.warning("[AP layout] Falling back to OpenCV after count mismatch.")
                return None

            break

        assert obj is not None
        receipts = obj.get("receipts")
        assert isinstance(receipts, list)

        regions = _layout_boxes_to_pixel_regions(
            receipts, full_w, full_h, AP_VLM_LAYOUT_BOX_PAD_PCT,
        )
        if len(regions) < 2:
            logger.warning("[AP layout] Fewer than 2 pixel regions after pad; falling back to OpenCV.")
            return None

        logger.info(
            "[AP layout] Using VLM boxes: confidence=%.2f regions=%s attempts_used=%s",
            float(obj.get("confidence", 0)),
            len(regions),
            attempt + 1,
        )
        return regions
    except Exception as exc:
        logger.warning(
            "[AP layout] VLM layout failed: %s; %s.",
            exc,
            "no OpenCV fallback" if vlm_only else "falling back to OpenCV",
            exc_info=True,
        )
        return None
    finally:
        if thumb_path and os.path.isfile(thumb_path):
            try:
                os.remove(thumb_path)
            except OSError:
                pass


async def _run_ap_multi_with_guess_autoconfirm(
    image_path: str,
    *,
    trace_id: str,
    filename: str,
    ocr_provider_name: str,
    ocr_model_override: str,
    ocr_prompt_override: str | None,
    processing_mode: str,
    multi_receipt_confirmed: bool,
    ap_receipt_signal: str,
    pdf_page_num: int = 1,
    background_job_id: str | None = None,
    multi_receipt_kwargs: dict | None = None,
) -> tuple[dict | None, bool]:
    """
    Run multi-receipt OCR. Returns (result, ask_confirmation).

    Guess mode never asks the user: if the first pass cannot separate regions,
    automatically retry once with confirmed=True (force-split). Explicit
    multi_per_page is already confirmed by callers; single_* should not reach here.
    """
    kwargs = dict(multi_receipt_kwargs or {})
    result = await _run_ap_multi_receipt_ocr_from_image(
        image_path,
        trace_id=trace_id,
        filename=filename,
        ocr_provider_name=ocr_provider_name,
        ocr_model_override=ocr_model_override,
        ocr_prompt_override=ocr_prompt_override,
        processing_mode=processing_mode,
        confirmed=multi_receipt_confirmed,
        pdf_page_num=pdf_page_num,
        background_job_id=background_job_id,
        **kwargs,
    )
    if result is not None or multi_receipt_confirmed:
        return result, False
    if is_vlm_detection_backend():
        return result, False
    if (ap_receipt_signal or "guess").strip().lower() != "guess":
        return None, True
    logger.info(
        "[ROUTER] Guess auto-confirm: classifier suspected multi-receipt but OpenCV "
        "could not separate regions — retrying with force-split (no user prompt).",
    )
    result = await _run_ap_multi_receipt_ocr_from_image(
        image_path,
        trace_id=trace_id,
        filename=filename,
        ocr_provider_name=ocr_provider_name,
        ocr_model_override=ocr_model_override,
        ocr_prompt_override=ocr_prompt_override,
        processing_mode=processing_mode,
        confirmed=True,
        pdf_page_num=pdf_page_num,
        background_job_id=background_job_id,
        **kwargs,
    )
    return result, False


def _ap_stub_receipt_candidate_row(
    *,
    receipt_bbox: dict[str, int] | None,
    pdf_page_num: int,
    receipt_index: int,
    parent_image_size: tuple[int, int] | None,
    vlm_mode: bool,
    extra_flags: list[str] | None = None,
) -> dict[str, Any]:
    """One editable Table Review row for a crop even when VLM JSON is empty."""
    flags = list(extra_flags or [])
    if "incomplete_extraction" not in flags:
        flags.append("incomplete_extraction")
    row: dict[str, Any] = {
        "needs_review": True,
        "validation_flags": flags,
    }
    _extraction_validation.attach_receipt_region_provenance(
        row,
        receipt_bbox=receipt_bbox,
        pdf_page_num=pdf_page_num,
        parent_image_size=parent_image_size,
        segmentation_mode="vlm_detect" if vlm_mode else None,
        segmentation_source="vlm_layout" if vlm_mode else None,
        crop_status="verified_vlm_crop" if vlm_mode else None,
        receipt_instance_id=receipt_instance_id(pdf_page_num, receipt_index),
    )
    return row


def _public_ap_receipt_page(sub: Mapping[str, Any], default_page: int) -> dict[str, Any]:
    """Keep crop identity when flattening M-VDU pages for Table Review + live preview."""
    page = sub.get("page", default_page)
    try:
        page_num = int(page) if page is not None else int(default_page)
    except (TypeError, ValueError):
        page_num = int(default_page)
    out: dict[str, Any] = {
        "page": page_num,
        "text": sub.get("text", ""),
        "lines_count": sub.get("lines_count", 0),
        "extracted_fields": sub.get("extracted_fields", {}),
        "field_confidence": sub.get("field_confidence", 0),
        "ai_enhanced": sub.get("ai_enhanced"),
    }
    for key in (
        "receipt_index",
        "receipt_instance_id",
        "receipt_bbox",
        "image_quality",
        "crop_status",
        "segmentation_mode",
        "segmentation_source",
        "status",
        "error_code",
        "error_detail",
        "needs_split_review",
    ):
        if key in sub and sub[key] is not None:
            out[key] = sub[key]
    idx = out.get("receipt_index")
    if out.get("receipt_instance_id") is None and idx is not None:
        try:
            out["receipt_instance_id"] = receipt_instance_id(page_num, int(idx))
        except (TypeError, ValueError):
            pass
    return out


def _vlm_split_review_page(page_num: int, *, message: str | None = None) -> dict[str, Any]:
    return {
        "page": page_num,
        "status": "error",
        "error_code": "NEEDS_SPLIT_REVIEW",
        "error_detail": message
        or "AI layout did not return usable receipt boxes. Review the source page.",
        "needs_split_review": True,
        "crop_status": "needs_split_review",
        "segmentation_mode": "vlm_detect",
        "segmentation_source": "vlm_layout",
        "text": "",
        "lines_count": 0,
        "extracted_fields": {},
        "field_confidence": 0.0,
        "ai_enhanced": None,
    }


async def _run_ap_multi_receipt_ocr_from_image(
    image_path: str,
    *,
    trace_id: str,
    filename: str,
    ocr_provider_name: str,
    ocr_model_override: str,
    ocr_prompt_override: str | None,
    processing_mode: str,
    confirmed: bool = False,
    pdf_page_num: int = 1,
    background_job_id: str | None = None,
    rescan_supplement: str | None = None,
    expected_receipt_count: int | None = None,
    count_assertion_strength: str = "unknown",
    prefer_denser_split: bool = False,
) -> dict | None:
    """
    pdf_page_num: the actual PDF page this image came from (1-based).
    Used so that file_position in the TSV shows the correct PDF page number
    rather than the receipt-region index within the page.
    """
    _raise_if_bg_job_cancelled(background_job_id)
    expected = normalize_expected_receipt_count(expected_receipt_count)
    strength = (count_assertion_strength or "unknown").strip().lower()
    if strength not in ("hard", "soft", "unknown"):
        strength = "unknown"
    receipt_regions: list[dict[str, int]] = []
    layout_ok = False
    vlm_mode = True
    vlm_regs = await _ap_vlm_layout_try_receipt_regions(
        image_path,
        ocr_provider_name=ocr_provider_name,
        ocr_model_override=ocr_model_override,
        background_job_id=background_job_id,
        rescan_supplement=rescan_supplement,
        expected_receipt_count=expected,
        vlm_only=True,
    )
    if not vlm_regs:
        logger.info(
            "[ocr_metrics] seg_source=vlm_layout opencv_calls=0 needs_split_review=1",
        )
        return vlm_split_review_payload(
            trace_id=trace_id,
            filename=filename,
            processing_mode=processing_mode,
            reason="empty_or_malformed",
        )
    receipt_regions = vlm_regs
    layout_ok = True
    logger.info(
        "[ocr_metrics] seg_source=vlm_layout opencv_calls=0 candidate_regions=%s",
        len(receipt_regions),
    )

    seg_source = "vlm_layout"
    if (not vlm_mode) and len(receipt_regions) > 1 and not confirmed:
        try:
            from PIL import Image

            with Image.open(image_path) as _im:
                page_w, page_h = _im.size
            keep_multi, reason, stats = _multi_region_evidence(
                receipt_regions,
                page_w=page_w,
                page_h=page_h,
            )
            logger.info(
                "[ocr_metrics] seg_source=%s candidate_regions=%s keep_multi=%s reason=%s stats=%s",
                seg_source,
                len(receipt_regions),
                keep_multi,
                reason,
                stats,
            )
            if not keep_multi:
                merged = _merge_regions_to_single(
                    receipt_regions,
                    page_w=page_w,
                    page_h=page_h,
                )
                receipt_regions = [merged]
                logger.info(
                    "[AP] Collapsed over-split regions to single region (reason=%s).",
                    reason,
                )
        except Exception as exc:
            logger.warning("[AP] single-receipt guard failed: %s", exc)

    if (not vlm_mode) and len(receipt_regions) <= 1:
        if not confirmed:
            return None
        # User explicitly confirmed multiple receipts — force-split the image
        # even though auto-detection found only one region.
        logger.warning(
            "[AP] Auto-detection found ≤1 region but user confirmed multi-receipt; "
            "attempting forced split of image.",
        )
        receipt_regions = _force_split_receipt_regions(
            image_path,
            expected_receipt_count=expected,
        )
        seg_source = "force_split"
        if len(receipt_regions) < 2:
            logger.warning(
                "[AP] Forced split also failed; falling back to single-receipt processing.",
            )
            return None

    # Count-aware / missed-receipt recovery: prefer denser or count-matching geometry
    # when baseline under-segments (e.g. 3 tall columns for a 3x3 page of slips).
    if (not vlm_mode) and (confirmed or prefer_denser_split or expected is not None):
        forced = _force_split_receipt_regions(
            image_path,
            expected_receipt_count=expected,
        )
        if forced and len(forced) >= 2:
            use_forced = False
            if expected is not None:
                cur_gap = abs(len(receipt_regions) - expected)
                forced_gap = abs(len(forced) - expected)
                if forced_gap < cur_gap or (
                    forced_gap == cur_gap and len(forced) > len(receipt_regions)
                ):
                    use_forced = True
            elif prefer_denser_split and len(forced) > len(receipt_regions):
                use_forced = True
            elif confirmed and len(forced) > len(receipt_regions):
                # Multi-receipt confirmed: prefer denser H×V evidence over sparse OpenCV boxes.
                use_forced = True
            if use_forced:
                logger.info(
                    "[AP] Count-aware recovery: %s regions (%s) → %s regions (force_split)",
                    len(receipt_regions),
                    seg_source,
                    len(forced),
                )
                receipt_regions = forced
                seg_source = "force_split_count_recovery"

    # Drop near-blank margin strips before OCR (N-agnostic noise rejection).
    if (not vlm_mode) and len(receipt_regions) >= 2:
        filtered = _filter_credible_receipt_regions(image_path, receipt_regions)
        if filtered and len(filtered) != len(receipt_regions):
            logger.info(
                "[AP] Low-ink filter: %s → %s credible regions",
                len(receipt_regions),
                len(filtered),
            )
            receipt_regions = filtered
            if seg_source and "ink_filter" not in seg_source:
                seg_source = f"{seg_source}+ink_filter"

    if (not vlm_mode) and len(receipt_regions) < 2:
        if not confirmed:
            return None
        logger.warning(
            "[AP] Fewer than 2 credible regions after ink filter; cannot multi-split.",
        )
        return None

    if vlm_mode and not receipt_regions:
        return vlm_split_review_payload(
            trace_id=trace_id,
            filename=filename,
            processing_mode=processing_mode,
            reason="empty_after_hygiene",
        )

    logger.info(
        "[AP] Detected %s receipt regions (source=%s); processing as multi-receipt image "
        "(crop_concurrency=%s expected_count=%s).",
        len(receipt_regions),
        seg_source,
        AP_CROP_OCR_CONCURRENCY,
        expected,
    )
    cropped_paths = _crop_receipt_regions(image_path, receipt_regions)
    n_crops = len(cropped_paths)
    quality_temp_paths: list[str] = []
    page_quality: dict[str, Any] | None = None
    if _receipt_image_quality.quality_enabled():
        try:
            page_quality = _receipt_image_quality.probe_page(image_path)
        except Exception as exc:
            logger.warning("[AQ] page quality probe failed: %s", exc)
            page_quality = {"error": str(exc)[:300]}
    sem = asyncio.Semaphore(AP_CROP_OCR_CONCURRENCY)
    crop_pass_image_options: dict | None = None
    if AP_CROP_OCR_IMAGE_MAX_SIDE > 0:
        crop_pass_image_options = {
            "max_side": AP_CROP_OCR_IMAGE_MAX_SIDE,
            "format": "JPEG",
            "quality": AP_CROP_OCR_JPEG_QUALITY,
        }
    skipped_rows: list[dict[str, Any]] = []

    def _crop_guard_reason(receipt_bbox: dict[str, int]) -> str | None:
        w = int(receipt_bbox.get("w", 0))
        h = int(receipt_bbox.get("h", 0))
        if w < AP_CROP_MIN_WIDTH_PX or h < AP_CROP_MIN_HEIGHT_PX:
            return "CROP_TOO_SMALL"
        area = w * h
        if area < AP_CROP_MIN_AREA_PX:
            return "CROP_TOO_SMALL"
        ratio = (w / h) if h > 0 else 0.0
        if ratio < AP_CROP_MIN_ASPECT_RATIO or ratio > AP_CROP_MAX_ASPECT_RATIO:
            return "CROP_BAD_ASPECT"
        return None

    async def _process_single_crop(
        page_num: int,
        crop_path: str,
        receipt_bbox: dict[str, int],
    ) -> dict:
        async with sem:
            _raise_if_bg_job_cancelled(background_job_id)
            ocr_image_path = crop_path
            quality_audit: dict[str, Any] | None = None
            if _receipt_image_quality.quality_enabled():
                try:
                    prepared = _receipt_image_quality.prepare_crop_for_ocr(crop_path)
                    ocr_image_path = prepared.path
                    quality_audit = prepared.audit
                    for tp in prepared.temp_paths:
                        quality_temp_paths.append(tp)
                except Exception as exc:
                    logger.warning(
                        "   [AQ] crop %s quality prepare failed: %s",
                        page_num,
                        exc,
                    )
                    quality_audit = {
                        "enabled": True,
                        "selection": "original",
                        "error": str(exc)[:400],
                    }
            logger.info(
                "   [AP %s/%s] OCR pass 1/2 (document parsing) with %s model=%s%s...",
                page_num,
                n_crops,
                ocr_provider_name,
                AP_MULTI_RECEIPT_OCR_MODEL,
                (
                    f" aq={quality_audit.get('selection')}"
                    if isinstance(quality_audit, dict)
                    else ""
                ),
            )
            page_ocr_result = await _ocr_service.recognize(
                ocr_image_path,
                provider_name=ocr_provider_name,
                model=AP_MULTI_RECEIPT_OCR_MODEL,
                prompt_override=ocr_prompt_override or AP_MULTI_RECEIPT_DOCUMENT_PARSING_PROMPT,
                image_options=crop_pass_image_options,
            )
            _raise_if_bg_job_cancelled(background_job_id)
            filtered_result = _filtering_pipeline.filter_and_extract(page_ocr_result)
            pass1_text = page_ocr_result.text or ""

            from PIL import Image

            parent_wh: tuple[int, int] | None = None
            try:
                with Image.open(image_path) as _im:
                    parent_wh = _im.size
            except Exception:
                parent_wh = None

            ai_enhanced_fields = {"output_format": "tsv", "tsv_rows": [], "ai_processed": True}
            try:
                _raise_if_bg_job_cancelled(background_job_id)
                ai_enhanced_fields = await _extract_ar_ap_ai_fields_routed(
                    ocr_text=pass1_text,
                    img_path=ocr_image_path,
                    page_num=page_num,
                    ocr_provider_name=ocr_provider_name,
                    ocr_model_override=ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL,
                    processing_mode=processing_mode,
                    image_options=crop_pass_image_options,
                    ocr_lines=page_ocr_result.lines,
                    rescan_supplement=rescan_supplement,
                )
            except OcrBackgroundJobCancelled:
                raise
            except Exception as e:
                logger.warning(
                    "   [WARN] AP extraction failed for crop %s: %s",
                    page_num,
                    str(e),
                )
            ai_enhanced_fields = await _ap_apply_cross_vlm_merge_if_configured(
                processing_mode=processing_mode,
                primary_model=ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL,
                ai_primary=ai_enhanced_fields,
                ocr_text=pass1_text,
                img_path=ocr_image_path,
                page_num=page_num,
                ocr_provider_name=ocr_provider_name,
                image_options=crop_pass_image_options,
                ocr_lines=page_ocr_result.lines,
                cheque_probe=None,
                rescan_supplement=rescan_supplement,
            )
            instance_id = receipt_instance_id(pdf_page_num, page_num)
            for row in ai_enhanced_fields.get("tsv_rows") or []:
                if isinstance(row, dict):
                    _extraction_validation.attach_receipt_region_provenance(
                        row,
                        receipt_bbox=receipt_bbox,
                        pdf_page_num=pdf_page_num,
                        parent_image_size=parent_wh,
                        segmentation_mode="vlm_detect" if vlm_mode else None,
                        segmentation_source="vlm_layout" if vlm_mode else None,
                        crop_status="verified_vlm_crop" if vlm_mode else None,
                        receipt_instance_id=instance_id,
                    )
                    _receipt_image_quality.attach_image_quality_provenance(row, quality_audit)
            if not (ai_enhanced_fields.get("tsv_rows") or []):
                stub = _ap_stub_receipt_candidate_row(
                    receipt_bbox=receipt_bbox,
                    pdf_page_num=pdf_page_num,
                    receipt_index=page_num,
                    parent_image_size=parent_wh,
                    vlm_mode=vlm_mode,
                )
                _receipt_image_quality.attach_image_quality_provenance(stub, quality_audit)
                ai_enhanced_fields["tsv_rows"] = [stub]
                ai_enhanced_fields["ai_processed"] = True

            return {
                "page": pdf_page_num,
                "receipt_index": page_num,
                "receipt_instance_id": instance_id,
                "text": page_ocr_result.text,
                "lines_count": len(page_ocr_result.lines),
                "extracted_fields": filtered_result["fields"],
                "field_confidence": filtered_result["overall_confidence"],
                "ai_enhanced": ai_enhanced_fields,
                "receipt_bbox": receipt_bbox,
                "image_quality": quality_audit,
                "segmentation_mode": "vlm_detect" if vlm_mode else "opencv",
                "segmentation_source": "vlm_layout" if vlm_mode else (seg_source or "opencv"),
                "crop_status": "verified_vlm_crop" if vlm_mode else None,
            }

    all_pages_results: list[dict] = []
    crop_errors = 0

    try:
        crop_jobs: list[tuple[int, dict[str, int], asyncio.Task]] = []
        for page_num, crop_path in enumerate(cropped_paths, 1):
            receipt_bbox = receipt_regions[page_num - 1]
            reason = _crop_guard_reason(receipt_bbox)
            if reason and not vlm_mode:
                skipped_rows.append(
                    {
                        "page": pdf_page_num,
                        "receipt_index": page_num,
                        "receipt_instance_id": receipt_instance_id(pdf_page_num, page_num),
                        "status": "error",
                        "error_code": reason,
                        "error_detail": f"Skipped crop preflight: {reason}",
                        "text": "",
                        "lines_count": 0,
                        "extracted_fields": {},
                        "field_confidence": 0.0,
                        "ai_enhanced": None,
                        "receipt_bbox": receipt_bbox,
                    }
                )
                continue
            if reason and vlm_mode:
                logger.info(
                    "[AP] VLM crop %s failed preflight (%s); still OCR receipt_instance",
                    page_num,
                    reason,
                )
            crop_jobs.append(
                (
                    page_num,
                    receipt_bbox,
                    asyncio.create_task(
                        _process_single_crop(page_num, crop_path, receipt_bbox),
                    ),
                )
            )
        async_tasks = [job[2] for job in crop_jobs]
        if skipped_rows:
            logger.info(
                "[ocr_metrics] crop_skipped_count=%s total_crops=%s page=%s",
                len(skipped_rows),
                len(receipt_regions),
                pdf_page_num,
            )
        poll_crops: asyncio.Task[None] | None = None
        if background_job_id and async_tasks:
            poll_crops = asyncio.create_task(
                _poll_cancel_tasks(async_tasks, job_id=background_job_id),
            )
        try:
            raw_results = await asyncio.gather(*async_tasks, return_exceptions=True)
        finally:
            if poll_crops is not None:
                poll_crops.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll_crops
        for (receipt_index, _bbox, _task), r in zip(crop_jobs, raw_results):
            if isinstance(r, asyncio.CancelledError):
                if background_job_id and background_job_cancelled(background_job_id):
                    raise OcrBackgroundJobCancelled()
                raise r
            if isinstance(r, OcrBackgroundJobCancelled):
                raise r
            if isinstance(r, BaseException):
                crop_errors += 1
                logger.error(
                    "   [AP] Multi-receipt crop %s/%s failed: %s",
                    receipt_index,
                    n_crops,
                    r,
                )
                all_pages_results.append(
                    {
                        "page": pdf_page_num,
                        "receipt_index": receipt_index,
                        "receipt_instance_id": receipt_instance_id(pdf_page_num, receipt_index),
                        "status": "error",
                        "error_detail": str(r)[:4000],
                        "text": "",
                        "lines_count": 0,
                        "extracted_fields": {},
                        "field_confidence": 0.0,
                        "ai_enhanced": None,
                        "receipt_bbox": _bbox,
                        "segmentation_mode": "vlm_detect" if vlm_mode else "opencv",
                        "segmentation_source": "vlm_layout" if vlm_mode else (seg_source or "opencv"),
                    }
                )
            else:
                if isinstance(r, dict):
                    r.setdefault("status", "success")
                    r.setdefault(
                        "receipt_instance_id",
                        receipt_instance_id(pdf_page_num, receipt_index),
                    )
                all_pages_results.append(r)
        all_pages_results.extend(skipped_rows)
        crop_errors += len(skipped_rows)
        all_pages_results.sort(key=lambda row: row.get("receipt_index", 0))
        batch_rows: list[Any] = []
        parent_wh_for_stub: tuple[int, int] | None = None
        try:
            from PIL import Image

            with Image.open(image_path) as _im:
                parent_wh_for_stub = _im.size
        except Exception:
            parent_wh_for_stub = None
        for p in all_pages_results:
            if p.get("status") == "error":
                continue
            ae = p.get("ai_enhanced") or {}
            usable_rows: list[dict[str, Any]] = []
            review_rows: list[dict[str, Any]] = []
            for row in ae.get("tsv_rows") or []:
                if not isinstance(row, dict):
                    continue
                if not _ar_ap_row_has_business_signal(row):
                    # Empty / incomplete extraction must not become a normal AP/AR row.
                    flags = list(row.get("validation_flags") or []) if isinstance(row.get("validation_flags"), list) else []
                    if "incomplete_extraction" not in flags:
                        flags.append("incomplete_extraction")
                    row["validation_flags"] = flags
                    row["needs_review"] = True
                    review_rows.append(row)
                    continue
                usable_rows.append(row)
            if not usable_rows:
                p["status"] = "incomplete_extraction"
                p["error_code"] = "INCOMPLETE_EXTRACTION"
                p["error_detail"] = "Region produced no usable amount/identity fields"
                crop_errors += 1
                if not review_rows:
                    try:
                        ridx = int(p.get("receipt_index") or 1)
                    except (TypeError, ValueError):
                        ridx = 1
                    review_rows = [
                        _ap_stub_receipt_candidate_row(
                            receipt_bbox=p.get("receipt_bbox"),
                            pdf_page_num=pdf_page_num,
                            receipt_index=ridx,
                            parent_image_size=parent_wh_for_stub,
                            vlm_mode=vlm_mode,
                        )
                    ]
                if isinstance(ae, dict):
                    ae["tsv_rows"] = review_rows
                    p["ai_enhanced"] = ae
                batch_rows.extend(review_rows)
            else:
                batch_rows.extend(usable_rows)
                if isinstance(ae, dict):
                    ae["tsv_rows"] = usable_rows
                    p["ai_enhanced"] = ae
        _extraction_validation.apply_batch_duplicate_flags_ar_ap(batch_rows)
    finally:
        for crop_path in cropped_paths:
            try:
                if os.path.exists(crop_path):
                    os.remove(crop_path)
            except Exception:
                pass
        for qpath in quality_temp_paths:
            try:
                if os.path.exists(qpath):
                    os.remove(qpath)
            except Exception:
                pass

    n_crop = len(cropped_paths)
    if crop_errors == 0:
        _mr_job_outcome = "ok"
    elif n_crop > 0 and crop_errors >= n_crop:
        _mr_job_outcome = "failed"
    else:
        _mr_job_outcome = "partial"

    extracted_region_count = sum(
        1
        for p in all_pages_results
        if isinstance(p, dict) and p.get("status") not in ("error", "incomplete_extraction")
    )
    count_status = "not_asserted"
    if expected is not None:
        count_status = (
            "matched"
            if len(receipt_regions) == expected and extracted_region_count == expected
            else "mismatch"
        )
    count_validation = {
        "expected_receipt_count": expected,
        "assertion_strength": strength if expected is not None else "unknown",
        "candidate_region_count": len(receipt_regions),
        "accepted_region_count": len(receipt_regions),
        "extracted_region_count": extracted_region_count,
        "seg_source": seg_source,
        "status": count_status,
    }

    return {
        "trace_id": trace_id,
        "filename": filename,
        "document_type": "multi_page_pdf",
        "total_pages": len(cropped_paths),
        "pages": all_pages_results,
        "ocr_job_outcome": _mr_job_outcome,
        "provider": ocr_provider_name,
        "processing_mode": processing_mode,
        "count_validation": count_validation,
        "page_image_quality": page_quality,
        "processing_steps": {
            "multi_receipt_split": "completed",
            "ocr_provider": ocr_provider_name,
            "pages_processed": len(all_pages_results),
            "structured_ocr_second_pass": "completed",
            "seg_source": seg_source,
            "receipt_image_quality": (
                "enabled" if _receipt_image_quality.quality_enabled() else "disabled"
            ),
        }
    }


def _convert_pdf_to_images(pdf_path: str) -> List[str]:
    """
    Convert PDF to images (one per page) using PyMuPDF
    Returns list of temporary image file paths
    """
    if not PDF_SUPPORT:
        raise RuntimeError("PDF support not available. Install: pip install PyMuPDF")
    
    try:
        logger.info(f"[PDF] Converting PDF to images using PyMuPDF...")
        
        # Convert PDF pages to images using PyMuPDF
        images_list = convert_pdf_to_images_list(pdf_path, target_format='PNG')
        
        # Extract just the image paths
        image_paths = [img['image_path'] for img in images_list]
        
        logger.info(f"[PDF] Converted to {len(image_paths)} page(s)")
        return image_paths
        
    except Exception as e:
        logger.error(f"PDF conversion failed: {str(e)}")
        raise RuntimeError(f"Failed to convert PDF: {str(e)}")


def _expand_scenario_d_inner_to_public_pages(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn _process_one_page-shaped dict into client-facing page row(s)."""
    merged: list[dict[str, Any]] = []
    if raw.get("_multi"):
        for sp in raw.get("_pages") or []:
            if isinstance(sp, dict):
                sp = dict(sp)
                sp.setdefault("status", "success")
                merged.append(sp)
        return merged
    row = dict(raw)
    row.pop("_events", None)
    row.pop("_multi", None)
    row.pop("_pages", None)
    row.setdefault("status", "success")
    merged.append(row)
    return merged


def recompute_ocr_job_outcome_from_pages(pages: list[dict[str, Any]]) -> str:
    has_success = any(p.get("status") != "error" for p in pages if isinstance(p, dict))
    has_error = any(p.get("status") == "error" for p in pages if isinstance(p, dict))
    if has_error and not has_success:
        return "failed"
    if has_error:
        return "partial"
    return "ok"


def _classify_ocr_error_code(exc: BaseException) -> str:
    msg = str(exc).upper()
    if "OCR_HTTP_429" in msg or "TOO MANY REQUESTS" in msg:
        return "VLM_RATE_LIMIT"
    if "OCR_EMPTY_CONTENT" in msg:
        return "VLM_EMPTY_CONTENT"
    if "OCR_REQUEST_ERROR" in msg or "TIMEOUT" in msg or "CONNECTION" in msg:
        return "VLM_REQUEST_FAILED"
    if "CROP_TOO_SMALL" in msg:
        return "CROP_TOO_SMALL"
    return "PAGE_PROCESSING_FAILED"


def _build_error_page_row(page_num: int, exc: BaseException) -> dict[str, Any]:
    return {
        "page": page_num,
        "status": "error",
        "error_code": _classify_ocr_error_code(exc),
        "error_detail": str(exc)[:4000],
        "text": "",
        "lines_count": 0,
        "extracted_fields": {},
        "field_confidence": 0.0,
        "ai_enhanced": None,
    }


def _scenario_d_termination_reason(
    *,
    consecutive_failures: int,
    failed_pages: int,
    observed_pages: int,
    saw_rate_limit: bool,
) -> str | None:
    if saw_rate_limit:
        return "rate_limited"
    if (
        OCR_SCENARIO_D_MAX_CONSECUTIVE_FAILURES > 0
        and consecutive_failures >= OCR_SCENARIO_D_MAX_CONSECUTIVE_FAILURES
    ):
        return "too_many_page_failures"
    if observed_pages >= OCR_SCENARIO_D_FAILURE_RATIO_MIN_SAMPLES:
        ratio = failed_pages / max(1, observed_pages)
        if ratio >= OCR_SCENARIO_D_MAX_FAILURE_RATIO:
            return "too_many_page_failures"
    return None


def _merge_regions_to_single(
    regions: list[dict[str, int]],
    *,
    page_w: int,
    page_h: int,
) -> dict[str, int]:
    x0 = min(int(r.get("x", 0)) for r in regions)
    y0 = min(int(r.get("y", 0)) for r in regions)
    x1 = max(int(r.get("x", 0)) + int(r.get("w", 0)) for r in regions)
    y1 = max(int(r.get("y", 0)) + int(r.get("h", 0)) for r in regions)
    pad_x = int(page_w * AP_SEG_SINGLE_MERGE_PAD_FRAC)
    pad_y = int(page_h * AP_SEG_SINGLE_MERGE_PAD_FRAC)
    x0 = max(0, x0 - pad_x)
    y0 = max(0, y0 - pad_y)
    x1 = min(page_w, x1 + pad_x)
    y1 = min(page_h, y1 + pad_y)
    return {"x": x0, "y": y0, "w": max(1, x1 - x0), "h": max(1, y1 - y0)}


def _multi_region_evidence(
    regions: list[dict[str, int]],
    *,
    page_w: int,
    page_h: int,
) -> tuple[bool, str, dict[str, float]]:
    page_area = max(1, page_w * page_h)
    if len(regions) < 2:
        return False, "insufficient_regions", {"region_count": float(len(regions))}

    norm: list[dict[str, int]] = []
    for r in regions:
        x = int(r.get("x", 0))
        y = int(r.get("y", 0))
        w = int(r.get("w", 0))
        h = int(r.get("h", 0))
        if w <= 0 or h <= 0:
            continue
        norm.append({"x": x, "y": y, "w": w, "h": h})
    if len(norm) < 2:
        return False, "invalid_regions", {"region_count": float(len(norm))}

    areas = sorted((r["w"] * r["h"] for r in norm), reverse=True)
    total_area = max(1, sum(areas))
    largest_area = areas[0]
    largest_idx = max(range(len(norm)), key=lambda i: norm[i]["w"] * norm[i]["h"])
    largest = norm[largest_idx]
    dominance = float(largest_area) / float(total_area)
    min_region_area = page_area * AP_SEG_MULTI_MIN_REGION_AREA_FRAC
    strong_regions = sum(1 for a in areas if a >= min_region_area)
    fragment_regions = sum(1 for a in areas[1:] if a <= largest_area * AP_SEG_FRAGMENT_REL_AREA_MAX)

    lx0, ly0 = largest["x"], largest["y"]
    lx1, ly1 = lx0 + largest["w"], ly0 + largest["h"]
    nested_regions = 0
    for i, r in enumerate(norm):
        if i == largest_idx:
            continue
        x0, y0 = r["x"], r["y"]
        x1, y1 = x0 + r["w"], y0 + r["h"]
        if x0 >= lx0 and y0 >= ly0 and x1 <= lx1 and y1 <= ly1:
            nested_regions += 1

    bounds = [(r["x"], r["x"] + r["w"], r["y"], r["y"] + r["h"]) for r in norm]
    x_span = max(b[1] for b in bounds) - min(b[0] for b in bounds)
    y_span = max(b[3] for b in bounds) - min(b[2] for b in bounds)
    horizontal = x_span >= y_span
    if horizontal:
        ordered = sorted(norm, key=lambda r: r["x"])
        gaps = [
            ordered[i + 1]["x"] - (ordered[i]["x"] + ordered[i]["w"])
            for i in range(len(ordered) - 1)
        ]
        gap_ratio = max(0.0, max(gaps, default=0) / float(max(1, page_w)))
    else:
        ordered = sorted(norm, key=lambda r: r["y"])
        gaps = [
            ordered[i + 1]["y"] - (ordered[i]["y"] + ordered[i]["h"])
            for i in range(len(ordered) - 1)
        ]
        gap_ratio = max(0.0, max(gaps, default=0) / float(max(1, page_h)))

    stats = {
        "region_count": float(len(norm)),
        "strong_regions": float(strong_regions),
        "dominance": float(dominance),
        "nested_regions": float(nested_regions),
        "fragment_regions": float(fragment_regions),
        "max_gap_ratio": float(gap_ratio),
    }

    if strong_regions < 2:
        return False, "weak_region_area", stats
    if dominance > AP_SEG_MULTI_MAX_DOMINANCE:
        return False, "dominant_region", stats
    if gap_ratio < AP_SEG_MIN_GAP_FRAC:
        return False, "weak_inter_region_gap", stats
    if nested_regions > 0 and fragment_regions >= len(norm) - 1:
        return False, "nested_fragments", stats
    return True, "strong_multi_evidence", stats


async def retry_scenario_d_pdf_page(
    *,
    pdf_path: str,
    page_num: int,
    processing_mode: str,
    multi_receipt_confirmed: bool,
    company_id: str,
    trace_id: str,
    filename: str,
    db: Session,
    background_job_id: str | None = None,
) -> list[dict[str, Any]]:
    """
    Re-run Scenario-D-style processing for one PDF page; returns new page row dict(s)
    for merging into an existing multi_page_pdf result (public shape).
    """
    img_path = convert_one_pdf_page_to_temp_png(pdf_path, page_num)
    try:
        if processing_mode == "AR":
            ocr_provider_name = AR_OCR_MODEL
            ocr_model_override = AR_OCR_MODEL
        elif processing_mode == "AP":
            ocr_provider_name = settings.ocr_provider
            ocr_model_override = AP_VLM_MODEL
        else:
            ocr_provider_name = settings.ocr_provider
            ocr_model_override = (
                BANK_VLM_MODEL if processing_mode == "BANK"
                else settings.vlm_model
            )

        ocr_prompt_override = (
            BANK_TABLE_PARSING_PROMPT
            if processing_mode == "BANK"
            else (AP_MULTI_RECEIPT_DOCUMENT_PARSING_PROMPT if processing_mode in ("AR", "AP") else None)
        )
        _stage1_rule_md = _load_rule_memory_for_ocr(db, company_id, processing_mode)
        _stage1_hints = _extract_ai_instructions(_stage1_rule_md)
        _profile_summary = _load_profile_summary_for_ocr(db, company_id)
        if ocr_prompt_override:
            if _profile_summary:
                ocr_prompt_override = (
                    f"[COMPANY CONTEXT: {_profile_summary}]\n\n" + ocr_prompt_override
                )
            if _stage1_hints:
                ocr_prompt_override = (
                    ocr_prompt_override
                    + "\n\n[COMPANY DOCUMENT HINTS]\n"
                    + _stage1_hints
                    + "\n\nApply the above hints when reading known vendor documents.\n"
                )

        _rule_md_pre = _load_rule_memory_for_ocr(db, company_id, processing_mode)
        _excl_rules_pre = _load_exclusion_rules_for_ocr(db, company_id)

        if processing_mode in ("AR", "AP"):
            multi_result = await _run_ap_multi_receipt_ocr_from_image(
                img_path,
                trace_id=trace_id,
                filename=filename or "",
                ocr_provider_name=ocr_provider_name,
                ocr_model_override=ocr_model_override,
                ocr_prompt_override=ocr_prompt_override,
                processing_mode=processing_mode,
                confirmed=True,
                pdf_page_num=page_num,
                background_job_id=background_job_id,
            )
            if multi_result is None:
                raise RuntimeError("multi_receipt_ocr_returned_none")
            if multi_result.get("needs_split_review"):
                raw_inner = _vlm_split_review_page(
                    page_num,
                    message=str(multi_result.get("message") or ""),
                )
            else:
                sub_list = multi_result.get("pages") or []
                raw_inner = {"_multi": True, "_pages": list(sub_list)}
        else:
            ocr_result = await _ocr_service.recognize(
                img_path,
                provider_name=ocr_provider_name,
                model=ocr_model_override,
                prompt_override=ocr_prompt_override,
            )
            filtered_result = _filtering_pipeline.filter_and_extract(ocr_result)
            ai_enhanced_fields = None
            if _ai_processor.api_key:
                try:
                    detected_type = _document_type_for_enhancement(processing_mode, ocr_result.text)
                    company_context = _load_company_context(db, company_id, detected_type)
                    ai_enhanced = await _ai_processor.enhance_ocr_result(
                        ocr_result,
                        document_type=detected_type,
                        processing_mode=processing_mode,
                        metadata={"company_context": company_context},
                    )
                    ai_enhanced_fields = ai_enhanced
                    if isinstance(ai_enhanced_fields, dict):
                        _inject_trace_meta(ai_enhanced_fields, trace_id=trace_id)
                        ctx = ai_enhanced_fields.get("context_meta") or {}
                        if not isinstance(ctx, dict):
                            ctx = {}
                        ctx["rule_memory_mode"] = processing_mode
                        ai_enhanced_fields["context_meta"] = ctx
                        if isinstance(ai_enhanced_fields.get("transactions"), list):
                            ai_enhanced_fields["transactions"] = _apply_rules_from_memory(
                                ai_enhanced_fields["transactions"],
                                _rule_md_pre,
                                ocr_result.text,
                            )
                            if _excl_rules_pre:
                                ai_enhanced_fields["transactions"] = _apply_exclusions(
                                    ai_enhanced_fields["transactions"],
                                    _excl_rules_pre,
                                    ocr_result.text,
                                    processing_mode,
                                    db,
                                )
                except Exception as exc:
                    logger.warning(
                        "   [WARN] AI enhancement failed on retry page %s: %s",
                        page_num,
                        exc,
                    )
            raw_inner = {
                "page": page_num,
                "text": ocr_result.text,
                "lines_count": len(ocr_result.lines),
                "extracted_fields": filtered_result["fields"],
                "field_confidence": filtered_result["overall_confidence"],
                "ai_enhanced": ai_enhanced_fields,
            }

        return _expand_scenario_d_inner_to_public_pages(raw_inner)
    finally:
        try:
            os.unlink(img_path)
        except OSError:
            pass


async def merge_ocr_job_retry_page_result(
    *,
    existing: dict[str, Any],
    pdf_path: str,
    page_num: int,
    processing_mode: str,
    multi_receipt_confirmed: bool,
    company_id: str,
    trace_id: str,
    filename: str,
    db: Session,
) -> dict[str, Any]:
    """Replace all rows for pdf page_num and return updated full OCR result dict."""
    pages = existing.get("pages")
    if not isinstance(pages, list):
        raise ValueError("invalid existing result: pages")
    has_err = any(
        isinstance(p, dict) and int(p.get("page", -1)) == page_num and p.get("status") == "error"
        for p in pages
    )
    if not has_err:
        raise ValueError("no error row for this page")
    new_rows = await retry_scenario_d_pdf_page(
        pdf_path=pdf_path,
        page_num=page_num,
        processing_mode=processing_mode,
        multi_receipt_confirmed=multi_receipt_confirmed,
        company_id=company_id,
        trace_id=trace_id,
        filename=filename,
        db=db,
        background_job_id=None,
    )
    kept = [
        p
        for p in pages
        if not (isinstance(p, dict) and int(p.get("page", -1)) == page_num)
    ]
    merged = kept + new_rows

    def _sort_key(entry: dict[str, Any]) -> tuple[int, int]:
        pi = int(entry.get("page") or 0)
        ri = entry.get("receipt_index")
        try:
            ri_int = int(ri) if ri is not None else 0
        except (TypeError, ValueError):
            ri_int = 0
        return pi, ri_int

    merged.sort(key=_sort_key)
    out = {**existing, "pages": merged}
    out["ocr_job_outcome"] = recompute_ocr_job_outcome_from_pages(merged)
    hist = out.get("ocr_retry_history")
    if not isinstance(hist, list):
        hist = []
    hist.append(
        {
            "page": page_num,
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "ok": True,
        }
    )
    out["ocr_retry_history"] = hist
    return out


def _extract_pdf_text_pages(pdf_path: str) -> List[dict]:
    """
    Extract selectable text per page using PyMuPDF.
    Returns list of dicts: {"page": int, "text": str}
    """
    if not PDF_SUPPORT:
        return []

    try:
        doc = fitz.open(pdf_path)
        pages = []
        for page_num in range(doc.page_count):
            page = doc[page_num]
            text = page.get_text("text") or ""
            pages.append({"page": page_num + 1, "text": text.strip()})
        doc.close()
        return pages
    except Exception as e:
        logger.warning(f"[PDF] Text extraction failed: {str(e)}")
        return []


def _build_text_ocr_result(text: str, source: str) -> OcrResult:
    lines = []
    for line in text.splitlines():
        line_text = line.strip()
        if not line_text:
            continue
        lines.append(
            OcrLine(
                text=line_text,
                confidence=0.9,
                bbox=[0, 0, 0, 0],
                words=[],
            )
        )
    return OcrResult(
        text=text,
        lines=lines,
        metadata={"source": source, "extraction": "pdf_text"},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Dynamic receipt segmentation (column-first dominant-gap algorithm)
# Ported from segment_receipts_dynamic.py (Manus AI report, 2026-02-24).
# Replaces the contour-based _detect_receipt_regions as the primary segmenter.
# ──────────────────────────────────────────────────────────────────────────────

# Tunable parameters (all page-relative where noted)
_SEG_CANNY_LOW  = 20
_SEG_CANNY_HIGH = 80
_SEG_SMOOTH_WINDOW = 60          # px — smoothing window for projection profile
_SEG_EMPTY_THRESH_FRAC = 0.15   # fraction of 90th-percentile density → "empty"
_SEG_MIN_COL_GAP_PX = 60        # min contiguous empty columns for vertical split
_SEG_MIN_ROW_GAP_PX = 100       # min contiguous empty rows for horizontal split
_SEG_MIN_COL_WIDTH_FRAC = 0.18  # column must be ≥18% of page width
_SEG_MIN_ROW_HEIGHT_FRAC = 0.08 # row segment must be ≥8% of page height
_SEG_DOMINANT_GAP_RATIO = 1.5   # largest gap must be ≥1.5× second-largest to split
_SEG_MERGE_PX = 120             # merge split lines closer than this
_SEG_MIN_DENSITY_FRAC = 0.25    # cell must have ≥25% of densest cell's edge density
_SEG_CROP_PAD = 30              # px padding around each crop


def _seg_find_gap_runs(projection, size: int, min_run_px: int,
                       border_frac: float = 0.10) -> list:
    """
    Find contiguous runs of 'empty' rows/columns (≥ min_run_px long) using a
    page-relative density threshold (15% of the 90th-percentile of the profile).
    Returns list of (run_start, run_end, run_length) sorted by length descending.

    The threshold is applied as: value < threshold (strict less-than) so that
    columns/rows at exactly zero are always treated as empty regardless of the
    90th-percentile calibration value.
    """
    import numpy as np
    border = int(size * border_frac)
    region = projection[border: size - border]
    p90 = float(np.percentile(region, 90))
    # Guard against a near-zero p90 (e.g. mostly blank page) to avoid
    # treating the entire image as one big gap.
    threshold = _SEG_EMPTY_THRESH_FRAC * max(p90, 1e-6)

    runs = []
    in_run, run_start = False, 0
    for i in range(border, size - border):
        val = float(projection[i])
        if val < threshold and not in_run:
            in_run, run_start = True, i
        elif val >= threshold and in_run:
            in_run = False
            rl = i - run_start
            if rl >= min_run_px:
                runs.append((run_start, i, rl))
    if in_run:
        rl = (size - border) - run_start
        if rl >= min_run_px:
            runs.append((run_start, size - border, rl))
    return sorted(runs, key=lambda x: -x[2])


def _seg_runs_to_splits(runs: list) -> list:
    """Convert gap runs to split positions (centres), merging nearby ones."""
    splits = [rs + rl // 2 for rs, re, rl in runs]
    merged = []
    for s in sorted(splits):
        if merged and (s - merged[-1]) < _SEG_MERGE_PX:
            merged[-1] = (merged[-1] + s) // 2
        else:
            merged.append(s)
    return merged


def _seg_find_col_splits(col_smooth, page_w: int) -> list:
    """Find vertical split positions; filter out columns narrower than MIN_COL_WIDTH_FRAC."""
    runs = _seg_find_gap_runs(col_smooth, page_w, _SEG_MIN_COL_GAP_PX)
    raw_splits = _seg_runs_to_splits(runs)
    min_col_w = int(page_w * _SEG_MIN_COL_WIDTH_FRAC)
    col_bounds = [0] + raw_splits + [page_w]
    # Validate every segment width (including the last column) and only keep splits
    # that separate two sufficiently wide columns.  The old code skipped the last
    # segment check (i < len - 2), leaving the rightmost column unvalidated.
    valid_splits = []
    for i in range(len(col_bounds) - 1):
        cw = col_bounds[i + 1] - col_bounds[i]
        if cw < min_col_w:
            # This segment is too narrow — drop the split that created it.
            if valid_splits:
                valid_splits.pop()
        elif i < len(col_bounds) - 2:
            valid_splits.append(col_bounds[i + 1])
    return valid_splits


def _seg_find_row_splits(row_smooth, page_h: int) -> list:
    """
    Find horizontal splits within a column strip.

    Two-mode gap acceptance to support a flexible number of stacked receipts:

    1. Uniform-spacing mode — when ALL discovered gaps are within
       DOMINANT_GAP_RATIO of each other (i.e. the smallest is ≥ 1/ratio of
       the largest), every gap is a true receipt boundary.  This handles 3 or
       more stacked receipts of similar height with equal margins where no
       single gap dominates.

    2. Dominant-gap mode — when gaps vary widely (the usual case for a single
       receipt's internal whitespace mixed with real separators), only accept
       gaps that are ≥ DOMINANT_GAP_RATIO × the next-smaller gap, stopping at
       the first non-dominant gap.  This prevents internal section spacing from
       being mistaken for a receipt boundary.

    Note: _SEG_MIN_ROW_GAP_PX already filters out small internal whitespace
    (e.g. line spacing, section padding within a receipt), so by the time runs
    reach here they are candidates for real separators.
    """
    runs = _seg_find_gap_runs(row_smooth, page_h, _SEG_MIN_ROW_GAP_PX)
    if not runs:
        return []
    if len(runs) == 1:
        return _seg_runs_to_splits(runs[:1])

    # runs is sorted by length descending; runs[0] is largest, runs[-1] smallest.
    largest_run = runs[0][2]
    smallest_run = runs[-1][2]

    if smallest_run * _SEG_DOMINANT_GAP_RATIO >= largest_run:
        # Uniform-spacing mode: all gaps are within the dominance band.
        # Accept every gap — they are all real receipt separators.
        accepted = list(runs)
    else:
        # Dominant-gap mode: gaps vary widely, apply the cascade filter.
        accepted = []
        for i, run in enumerate(runs):
            next_run_len = runs[i + 1][2] if i + 1 < len(runs) else 0
            if next_run_len == 0 or run[2] >= _SEG_DOMINANT_GAP_RATIO * next_run_len:
                accepted.append(run)
            else:
                break  # first non-dominant gap stops the cascade

    splits = _seg_runs_to_splits(accepted)
    # Filter: remove splits that would create rows shorter than minimum.
    # Validate every segment height (including the last row) — the old code skipped
    # the last segment check (i < len - 2), leaving the bottom row unvalidated.
    min_row_h = int(page_h * _SEG_MIN_ROW_HEIGHT_FRAC)
    row_bounds = [0] + splits + [page_h]
    valid_splits = []
    for i in range(len(row_bounds) - 1):
        rh = row_bounds[i + 1] - row_bounds[i]
        if rh < min_row_h:
            if valid_splits:
                valid_splits.pop()
        elif i < len(row_bounds) - 2:
            valid_splits.append(row_bounds[i + 1])
    return valid_splits


def _detect_receipt_regions_v2(image_path: str) -> list[dict[str, int]]:
    """
    Dynamic receipt segmentation using column-first dominant-gap detection.
    Self-calibrating: all thresholds are relative to the page's own 90th-percentile
    edge density, so they work across any scan brightness or DPI.

    Returns the same format as _detect_receipt_regions:
        [{"x": x1, "y": y1, "w": w, "h": h}, ...]
    Falls back to _detect_receipt_regions (PIL/contour) on any error.
    """
    try:
        import cv2
        import numpy as np
        from scipy.ndimage import uniform_filter1d
    except ImportError:
        logger.warning("[SEG-v2] scipy/cv2 not available — falling back to v1")
        return _detect_receipt_regions(image_path)

    try:
        img = cv2.imread(image_path)
        if img is None:
            raise FileNotFoundError(f"Cannot read: {image_path}")

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape

        # Mild Gaussian blur before Canny suppresses paper-texture and JPEG
        # artefact noise that would otherwise create spurious background edges,
        # causing whitespace gaps between receipts to appear non-empty.
        gray_blur = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(gray_blur, _SEG_CANNY_LOW, _SEG_CANNY_HIGH)

        # Column projection — find vertical splits (receipts side by side)
        col_proj = edges.sum(axis=0).astype(float) / (edges.shape[0] + 1e-9)
        col_smooth = uniform_filter1d(col_proj, size=_SEG_SMOOTH_WINDOW)
        split_cols = _seg_find_col_splits(col_smooth, w)
        col_bounds = [0] + split_cols + [w]

        logger.debug("[SEG-v2] %s: %d column(s), split_cols=%s",
                     image_path, len(col_bounds) - 1, split_cols)

        # Row projection — within each column strip, find horizontal splits
        all_cells = []
        for ci in range(len(col_bounds) - 1):
            x1, x2 = col_bounds[ci], col_bounds[ci + 1]
            strip = edges[:, x1:x2]
            row_proj = strip.sum(axis=1).astype(float) / (strip.shape[1] + 1e-9)
            row_smooth = uniform_filter1d(row_proj, size=_SEG_SMOOTH_WINDOW)
            split_rows = _seg_find_row_splits(row_smooth, h)
            row_bounds_strip = [0] + split_rows + [h]
            for ri in range(len(row_bounds_strip) - 1):
                y1, y2 = row_bounds_strip[ri], row_bounds_strip[ri + 1]
                all_cells.append((x1, y1, x2, y2))

        # Filter by relative edge density — remove near-empty cells
        densities = []
        for x1, y1, x2, y2 in all_cells:
            region = edges[y1:y2, x1:x2]
            densities.append(float(region.sum()) / (region.size + 1e-9) if region.size else 0.0)

        if densities:
            max_d = max(densities)
            min_d = max_d * _SEG_MIN_DENSITY_FRAC
            all_cells = [c for c, d in zip(all_cells, densities) if d >= min_d]

        logger.debug("[SEG-v2] %d receipt(s) after density filter", len(all_cells))

        if not all_cells:
            logger.warning("[SEG-v2] no cells found — falling back to v1")
            return _detect_receipt_regions(image_path)

        # Convert to the expected dict format {"x", "y", "w", "h"} with padding
        regions = []
        for x1, y1, x2, y2 in all_cells:
            rx1 = max(0, x1 + _SEG_CROP_PAD)
            ry1 = max(0, y1 + _SEG_CROP_PAD)
            rx2 = min(w, x2 - _SEG_CROP_PAD)
            ry2 = min(h, y2 - _SEG_CROP_PAD)
            if rx2 > rx1 and ry2 > ry1:
                regions.append({"x": rx1, "y": ry1, "w": rx2 - rx1, "h": ry2 - ry1})

        # Sort reading order: top-to-bottom, left-to-right
        regions.sort(key=lambda r: (r["y"] // 200, r["x"]))
        return regions

    except Exception as exc:
        logger.warning("[SEG-v2] segmentation failed (%s) — falling back to v1", exc)
        return _detect_receipt_regions(image_path)


def _detect_receipt_regions(image_path: str) -> list[dict[str, int]]:
    """
    Detect likely receipt rectangles in a single image page.
    Returns a list of bounding boxes sorted by reading order.
    """
    try:
        import cv2
    except Exception:
        cv2 = None

    if cv2 is None:
        return _detect_receipt_regions_pil(image_path)

    image = cv2.imread(image_path)
    if image is None:
        return []

    height, width = image.shape[:2]
    if width < 80 or height < 80:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 160)
    # Use a small kernel so nearby receipts are NOT merged together.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=2)
    dilated = cv2.dilate(closed, kernel, iterations=1)

    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    min_area = int(width * height * 0.015)
    max_area = int(width * height * 0.95)
    regions: list[dict[str, int]] = []

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        if area < min_area or area > max_area:
            continue
        aspect_ratio = w / h if h > 0 else 0.0
        if aspect_ratio < 0.10 or aspect_ratio > 10.0:
            continue

        # Expand a bit to include borders/text edges.
        pad_x = max(6, int(w * 0.02))
        pad_y = max(6, int(h * 0.02))
        x0 = max(0, x - pad_x)
        y0 = max(0, y - pad_y)
        x1 = min(width, x + w + pad_x)
        y1 = min(height, y + h + pad_y)
        regions.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0})

    # Remove near-duplicate overlaps by IoU.
    def iou(a: dict[str, int], b: dict[str, int]) -> float:
        ax1, ay1 = a["x"], a["y"]
        ax2, ay2 = ax1 + a["w"], ay1 + a["h"]
        bx1, by1 = b["x"], b["y"]
        bx2, by2 = bx1 + b["w"], by1 + b["h"]
        inter_w = max(0, min(ax2, bx2) - max(ax1, bx1))
        inter_h = max(0, min(ay2, by2) - max(ay1, by1))
        inter = inter_w * inter_h
        if inter <= 0:
            return 0.0
        union = a["w"] * a["h"] + b["w"] * b["h"] - inter
        return inter / union if union > 0 else 0.0

    deduped: list[dict[str, int]] = []
    for region in sorted(regions, key=lambda r: (r["y"], r["x"])):
        if any(iou(region, existing) > 0.80 for existing in deduped):
            continue
        deduped.append(region)

    # Filter out tiny noise blobs (< 8 % of the largest region's area).
    if deduped:
        max_area = max(r["w"] * r["h"] for r in deduped)
        deduped = [r for r in deduped if r["w"] * r["h"] >= max_area * 0.08]

    if len(deduped) >= 2:
        return deduped

    # OpenCV contour approach failed to split — fall back to PIL valley-scan
    # which also handles side-by-side and thin-border layouts.
    return _detect_receipt_regions_pil(image_path)


def _detect_receipt_regions_pil(image_path: str) -> list[dict[str, int]]:
    """
    Lightweight fallback detector (no OpenCV).
    Uses a local-contrast valley approach:
      fine_window_avg / context_window_avg < 0.35  →  separator gap
    This works even when receipts are separated only by a thin border line
    (not a zero-activity gap) and is robust against background scanner noise.
    Handles both stacked (top-bottom) and side-by-side (left-right) layouts.
    """
    from PIL import Image

    def _build_cumsum(activity: list[int]) -> list[int]:
        cs = [0] * (len(activity) + 1)
        for i, v in enumerate(activity):
            cs[i + 1] = cs[i] + v
        return cs

    def _range_avg(cs: list[int], lo: int, hi: int) -> float:
        n = len(cs) - 1
        lo, hi = max(0, lo), min(n, hi)
        if hi <= lo:
            return 0.0
        return (cs[hi] - cs[lo]) / (hi - lo)

    def _find_gap_splits(activity: list[int], total: int) -> list[int]:
        """
        Find inter-receipt split positions using a fine/context ratio test.

        For each scan position x:
          - fine  = average ink density in a ±1 % window around x
          - context = average ink density in a ±14 % window around x
          fine/context < 0.35 indicates a gap (valley) between receipts.

        Splits that have no receipt content on one side (outer margin) are
        discarded automatically via the min-content check.
        """
        if total < 40:
            return []

        ctx_win = max(20, total // 7)       # ≈ 14 % of width
        fine_win = max(5, total // 100)     # ≈  1 % of width
        check_win = max(40, total // 20)    # ≈  5 % — content check on each side
        noise_floor = 8                     # activity above scanner background (≈3)
        ratio_thresh = 0.35

        cs = _build_cumsum(activity)

        lo = int(total * 0.05)
        hi = int(total * 0.95)

        candidates: list[int] = []
        for x in range(lo, hi):
            ctx = _range_avg(cs, x - ctx_win, x + ctx_win + 1)
            if ctx <= 0:
                continue
            fine = _range_avg(cs, x - fine_win, x + fine_win + 1)
            if fine / ctx >= ratio_thresh:
                continue
            # Require receipt content on BOTH sides (rules out outer-margin edges).
            left_content = _range_avg(cs, x - check_win, x)
            right_content = _range_avg(cs, x + 1, x + check_win + 1)
            if left_content <= noise_floor or right_content <= noise_floor:
                continue
            candidates.append(x)

        # Merge nearby candidates (keep the one with the lowest fine/ctx ratio).
        if not candidates:
            return []
        merged: list[int] = []
        for c in candidates:
            if merged and c - merged[-1] <= 20:
                # Keep whichever has a deeper valley.
                prev = merged[-1]
                ctx_p = _range_avg(cs, prev - ctx_win, prev + ctx_win + 1)
                ctx_c = _range_avg(cs, c - ctx_win, c + ctx_win + 1)
                ratio_p = _range_avg(cs, prev - fine_win, prev + fine_win + 1) / ctx_p if ctx_p else 1
                ratio_c = _range_avg(cs, c - fine_win, c + fine_win + 1) / ctx_c if ctx_c else 1
                if ratio_c < ratio_p:
                    merged[-1] = c
            else:
                merged.append(c)
        return merged

    try:
        with Image.open(image_path) as img:
            gray = img.convert("L")
            orig_w, orig_h = gray.size
            if orig_w < 80 or orig_h < 80:
                return []

            # Downscale for faster scanning.
            scan_w = min(orig_w, 1400)
            ratio = scan_w / float(orig_w)
            scan_h = max(1, int(orig_h * ratio))
            gray_s = gray.resize((scan_w, scan_h)) if ratio < 1.0 else gray
            pixels = gray_s.load()
            ink_threshold = 245

            # Build row and column activity arrays separately.
            row_activity = [0] * scan_h
            for y in range(scan_h):
                for x in range(0, scan_w, 2):
                    if pixels[x, y] < ink_threshold:
                        row_activity[y] += 1

            col_activity = [0] * scan_w
            for x in range(scan_w):
                for y in range(0, scan_h, 2):
                    if pixels[x, y] < ink_threshold:
                        col_activity[x] += 1

            inv_x = orig_w / float(scan_w)
            inv_y = orig_h / float(scan_h)
            min_region_area = int(orig_w * orig_h * 0.008)
            noise_floor = 8

            def _content_bounds_h() -> tuple[int, int]:
                first = next((x for x in range(scan_w) if col_activity[x] > noise_floor), 0)
                last = next((x for x in range(scan_w - 1, -1, -1) if col_activity[x] > noise_floor), scan_w - 1)
                return first, last

            def _content_bounds_v() -> tuple[int, int]:
                first = next((y for y in range(scan_h) if row_activity[y] > noise_floor), 0)
                last = next((y for y in range(scan_h - 1, -1, -1) if row_activity[y] > noise_floor), scan_h - 1)
                return first, last

            def _box_from_col_band(x_lo: int, x_hi: int) -> dict[str, int] | None:
                """Build original-image bounding box for a vertical (x) ink band."""
                cs_r = _build_cumsum(row_activity)
                r_act = [0] * scan_h
                for y in range(scan_h):
                    for x in range(x_lo, x_hi, 2):
                        if pixels[x, y] < ink_threshold:
                            r_act[y] += 1
                ys = [y for y, c in enumerate(r_act) if c > 0]
                if not ys:
                    return None
                pad_x = max(4, int((x_hi - x_lo) * 0.015))
                pad_y = max(4, int((max(ys) - min(ys)) * 0.015))
                ox0 = int(max(0, x_lo - pad_x) * inv_x)
                oy0 = int(max(0, min(ys) - pad_y) * inv_y)
                ox1 = int(min(scan_w, x_hi + pad_x) * inv_x)
                oy1 = int(min(scan_h, max(ys) + pad_y) * inv_y)
                w, h = max(1, ox1 - ox0), max(1, oy1 - oy0)
                return {"x": ox0, "y": oy0, "w": w, "h": h} if w * h >= min_region_area else None

            def _box_from_row_band(y_lo: int, y_hi: int) -> dict[str, int] | None:
                """Build original-image bounding box for a horizontal (y) ink band."""
                c_act = [0] * scan_w
                for x in range(scan_w):
                    for y in range(y_lo, y_hi, 2):
                        if pixels[x, y] < ink_threshold:
                            c_act[x] += 1
                xs = [x for x, c in enumerate(c_act) if c > 0]
                if not xs:
                    return None
                pad_x = max(4, int((max(xs) - min(xs)) * 0.015))
                pad_y = max(4, int((y_hi - y_lo) * 0.015))
                ox0 = int(max(0, min(xs) - pad_x) * inv_x)
                oy0 = int(max(0, y_lo - pad_y) * inv_y)
                ox1 = int(min(scan_w, max(xs) + pad_x) * inv_x)
                oy1 = int(min(scan_h, y_hi + pad_y) * inv_y)
                w, h = max(1, ox1 - ox0), max(1, oy1 - oy0)
                return {"x": ox0, "y": oy0, "w": w, "h": h} if w * h >= min_region_area else None

            # --- Vertical scan: side-by-side (left-right) receipts ---
            v_splits = _find_gap_splits(col_activity, scan_w)
            v_regions: list[dict[str, int]] = []
            if v_splits:
                c_start, c_end = _content_bounds_h()
                # Clip band boundaries to actual content area.
                boundaries = [c_start] + [s for s in v_splits if c_start < s < c_end] + [c_end]
                for i in range(len(boundaries) - 1):
                    x_lo, x_hi = boundaries[i], boundaries[i + 1]
                    if x_hi - x_lo < int(scan_w * 0.05):
                        continue
                    box = _box_from_col_band(x_lo, x_hi)
                    if box:
                        v_regions.append(box)

            # --- Horizontal scan: stacked (top-bottom) receipts ---
            h_splits = _find_gap_splits(row_activity, scan_h)
            h_regions: list[dict[str, int]] = []
            if h_splits:
                r_start, r_end = _content_bounds_v()
                boundaries = [r_start] + [s for s in h_splits if r_start < s < r_end] + [r_end]
                for i in range(len(boundaries) - 1):
                    y_lo, y_hi = boundaries[i], boundaries[i + 1]
                    if y_hi - y_lo < int(scan_h * 0.05):
                        continue
                    box = _box_from_row_band(y_lo, y_hi)
                    if box:
                        h_regions.append(box)

            # Balanced policy: keep multi only with strong evidence; avoid strip fragmentation.
            v_keep, _, v_stats = _multi_region_evidence(
                v_regions,
                page_w=orig_w,
                page_h=orig_h,
            )
            h_keep, _, h_stats = _multi_region_evidence(
                h_regions,
                page_w=orig_w,
                page_h=orig_h,
            )
            logger.info(
                "[ocr_metrics] seg_source=pil_fallback v_regions=%s h_regions=%s v_keep=%s h_keep=%s v_dom=%.3f h_dom=%.3f",
                len(v_regions),
                len(h_regions),
                v_keep,
                h_keep,
                float(v_stats.get("dominance", 1.0)),
                float(h_stats.get("dominance", 1.0)),
            )
            if v_keep and not h_keep:
                return sorted(v_regions, key=lambda r: (r["x"], r["y"]))
            if h_keep and not v_keep:
                return sorted(h_regions, key=lambda r: (r["y"], r["x"]))
            if v_keep and h_keep:
                v_score = (
                    int(v_stats.get("strong_regions", 0)),
                    -float(v_stats.get("dominance", 1.0)),
                    -int(v_stats.get("region_count", 0)),
                )
                h_score = (
                    int(h_stats.get("strong_regions", 0)),
                    -float(h_stats.get("dominance", 1.0)),
                    -int(h_stats.get("region_count", 0)),
                )
                if v_score >= h_score:
                    return sorted(v_regions, key=lambda r: (r["x"], r["y"]))
                return sorted(h_regions, key=lambda r: (r["y"], r["x"]))
            return []
    except Exception:
        return []


def _grid_regions_from_axis_strips(
    v_regions: list[dict[str, int]],
    h_regions: list[dict[str, int]],
) -> list[dict[str, int]]:
    """Cartesian product of vertical × horizontal strips → page-native cells (any N)."""
    cells: list[dict[str, int]] = []
    for hr in h_regions:
        for vr in v_regions:
            cells.append(
                {
                    "x": int(vr["x"]),
                    "y": int(hr["y"]),
                    "w": int(vr["w"]),
                    "h": int(hr["h"]),
                }
            )
    return cells


def _region_ink_fraction(image_path: str, region: dict[str, int]) -> float:
    """Fraction of pixels darker than near-white in a page-native region (0..1)."""
    from PIL import Image

    try:
        with Image.open(image_path) as img:
            gray = img.convert("L")
            x = max(0, int(region.get("x", 0)))
            y = max(0, int(region.get("y", 0)))
            w = max(0, int(region.get("w", 0)))
            h = max(0, int(region.get("h", 0)))
            if w < 1 or h < 1:
                return 0.0
            crop = gray.crop((x, y, x + w, y + h))
            # Downsample for speed on large crops.
            max_side = 240
            cw, ch = crop.size
            scale = min(1.0, max_side / float(max(cw, ch)))
            if scale < 1.0:
                crop = crop.resize(
                    (max(1, int(cw * scale)), max(1, int(ch * scale))),
                    Image.BILINEAR,
                )
            hist = crop.histogram()
            dark = sum(hist[:245])
            total = sum(hist) or 1
            return dark / float(total)
    except Exception:
        return 0.0


def _filter_credible_receipt_regions(
    image_path: str,
    regions: list[dict[str, int]],
    *,
    min_ink_fraction: float | None = None,
) -> list[dict[str, int]]:
    """
    Drop near-blank / margin strips that are not physical receipts.

    N-agnostic: does not invent boxes; only removes low-ink noise regions.
    """
    thr = AP_SEG_MIN_INK_FRACTION if min_ink_fraction is None else float(min_ink_fraction)
    thr = max(0.0, min(thr, 0.5))
    kept: list[dict[str, int]] = []
    for reg in regions:
        if not isinstance(reg, dict):
            continue
        ink = _region_ink_fraction(image_path, reg)
        if ink < thr:
            logger.info(
                "[AP seg] drop low-ink region ink=%.4f thr=%.4f box=%s",
                ink,
                thr,
                reg,
            )
            continue
        kept.append(reg)
    return kept


def _refine_tall_column_regions(
    image_path: str,
    regions: list[dict[str, int]],
) -> list[dict[str, int]]:
    """
    Sub-split tall column strips when they contain stacked receipts.

    Runs force-split on each column-like crop and maps credible sub-boxes
    back to page coordinates. Keeps the parent when sub-split is not useful.
    """
    from PIL import Image

    if len(regions) < 1:
        return regions
    try:
        with Image.open(image_path) as img:
            page_w, page_h = img.size
    except Exception:
        return regions

    refined: list[dict[str, int]] = []
    changed = False
    for reg in regions:
        w = int(reg.get("w", 0))
        h = int(reg.get("h", 0))
        x = int(reg.get("x", 0))
        y = int(reg.get("y", 0))
        tall = page_h > 0 and (h / float(page_h)) >= 0.70
        narrow = page_w > 0 and (w / float(page_w)) <= 0.60
        if not (tall and narrow and h >= 200 and w >= 80):
            refined.append(reg)
            continue

        crop_path = None
        try:
            with Image.open(image_path) as img:
                crop = img.crop((x, y, x + w, y + h))
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".col-refine.png")
                crop_path = tmp.name
                tmp.close()
                crop.save(crop_path, format="PNG")
            sub = _force_split_receipt_regions(crop_path, refine_columns=False)
            sub = _filter_credible_receipt_regions(crop_path, sub) if sub else []
            # Only accept when the crop itself splits into multiple credible slips.
            if len(sub) >= 2:
                for s in sub:
                    refined.append(
                        {
                            "x": x + int(s["x"]),
                            "y": y + int(s["y"]),
                            "w": int(s["w"]),
                            "h": int(s["h"]),
                        }
                    )
                changed = True
            else:
                refined.append(reg)
        except Exception:
            refined.append(reg)
        finally:
            if crop_path and os.path.isfile(crop_path):
                try:
                    os.remove(crop_path)
                except OSError:
                    pass

    if not changed:
        return regions
    final = _filter_credible_receipt_regions(image_path, refined)
    return final if len(final) >= 2 else regions


def _ar_ap_row_has_business_signal(row: Mapping[str, Any]) -> bool:
    """Row-quality gate: amount plus at least one identity-ish field."""
    if not isinstance(row, Mapping):
        return False
    amount = str(row.get("amount") or "").strip()
    if not amount or amount in {"0", "0.0", "0.00"}:
        return False
    identity = (
        str(row.get("payee") or "").strip()
        or str(row.get("payer") or "").strip()
        or str(row.get("voucher_no") or "").strip()
        or str(row.get("date") or "").strip()
        or str(row.get("bank") or "").strip()
    )
    return bool(identity)


def _pick_force_split_hypothesis(
    v_regions: list[dict[str, int]],
    h_regions: list[dict[str, int]],
    *,
    expected_count: int | None = None,
    image_path: str | None = None,
) -> list[dict[str, int]]:
    """
    Choose among 1-axis strips and optional 2D grid without assuming a fixed layout.

    When both axes produce multi-region strips, the grid hypothesis is considered.
    An optional expected_count ranks hypotheses by closeness to N (7, 9, 10, …).
    When image_path is set, low-ink cells are filtered before ranking so empty
    margins do not win as "densest".
    """
    candidates: list[list[dict[str, int]]] = []
    if len(v_regions) > 1 and len(h_regions) > 1:
        grid = _grid_regions_from_axis_strips(v_regions, h_regions)
        if len(grid) > 1:
            candidates.append(grid)
    if len(v_regions) > 1:
        candidates.append(v_regions)
    if len(h_regions) > 1:
        candidates.append(h_regions)
    if not candidates:
        return []

    def _credible(cands: list[dict[str, int]]) -> list[dict[str, int]]:
        if not image_path:
            return list(cands)
        filtered = _filter_credible_receipt_regions(image_path, cands)
        return filtered if filtered else list(cands)

    scored: list[tuple[list[dict[str, int]], list[dict[str, int]]]] = [
        (raw, _credible(raw)) for raw in candidates
    ]

    expected = normalize_expected_receipt_count(expected_count)
    if expected is not None:
        exact = [(raw, cred) for raw, cred in scored if len(cred) == expected]
        if exact:
            # Prefer lower noise among exact matches.
            exact.sort(key=lambda pair: (len(pair[0]) - len(pair[1]), -len(pair[1])))
            return exact[0][1]
        scored.sort(
            key=lambda pair: (
                abs(len(pair[1]) - expected),
                len(pair[0]) - len(pair[1]),
                -len(pair[1]),
            )
        )
        return scored[0][1]

    # No asserted count: maximize credible regions, then minimize noise fraction.
    # Prefer cleaner proposals when a denser grid is mostly empty cells.
    def _mean_ink(regs: list[dict[str, int]]) -> float:
        if not image_path or not regs:
            return 0.0
        return sum(_region_ink_fraction(image_path, r) for r in regs) / float(len(regs))

    scored.sort(
        key=lambda pair: (
            -len(pair[1]),
            (len(pair[0]) - len(pair[1])) / max(1, len(pair[0])),
            -_mean_ink(pair[1]),
        )
    )
    best_raw, best_cred = scored[0]
    best_noise = (len(best_raw) - len(best_cred)) / max(1, len(best_raw))
    if best_noise >= 0.25:
        # Dense-but-noisy grid: prefer a cleaner candidate within 1 of best count.
        cleaner = [
            pair
            for pair in scored
            if (len(pair[0]) - len(pair[1])) / max(1, len(pair[0])) < 0.20
            and len(pair[1]) >= max(2, len(best_cred) - 1)
        ]
        if cleaner:
            cleaner.sort(
                key=lambda pair: (
                    (len(pair[0]) - len(pair[1])) / max(1, len(pair[0])),
                    -len(pair[1]),
                    -_mean_ink(pair[1]),
                )
            )
            return cleaner[0][1]
    return best_cred


def _force_split_receipt_regions(
    image_path: str,
    *,
    expected_receipt_count: int | None = None,
    refine_columns: bool = False,
) -> list[dict[str, int]]:
    """
    N-way splitter used when the user confirmed multiple receipts but automatic
    detection found only one region.

    Improvements over the old binary split:
    - Finds ALL significant whitespace gaps (not just the single largest one),
      enabling 3-way, 4-way, etc. splits for composite scans.
    - When BOTH horizontal and vertical gaps are credible, builds a 2D grid of
      cells (any rows×cols product) instead of discarding one axis.
    - Optional expected_receipt_count ranks hypotheses toward any N.
    - Low-ink / empty-margin cells are filtered before accepting a hypothesis.
    - Uses a noise-tolerant threshold instead of requiring exactly-zero ink,
      so JPEG/scanner artifacts in gap columns no longer break detection.
    - Searches across 5–95 % of the image (old code restricted to 30–70 %).
    - Falls back to a centre half-split only when no valid gaps are found.
    """
    from PIL import Image

    try:
        with Image.open(image_path) as img:
            gray = img.convert("L")
            orig_w, orig_h = gray.size
            if orig_w < 80 or orig_h < 80:
                return []

            scan_w = min(orig_w, 1400)
            ratio = scan_w / float(orig_w)
            scan_h = max(1, int(orig_h * ratio))
            if ratio < 1.0:
                gray = gray.resize((scan_w, scan_h))
            else:
                scan_w, scan_h = orig_w, orig_h

            pixels = gray.load()
            ink_threshold = 245

            # Build per-row and per-column ink counts (sample every 2nd pixel for speed).
            row_ink = [0] * scan_h
            col_ink = [0] * scan_w
            for y in range(scan_h):
                for x in range(0, scan_w, 2):
                    if pixels[x, y] < ink_threshold:
                        row_ink[y] += 1
                        col_ink[x] += 1

            def _find_all_gap_centres(
                activity: list[int],
                total: int,
                min_gap_px: int = 8,
                border_frac: float = 0.05,
                noise_frac: float = 0.03,
            ) -> list[int]:
                """
                Find the centre positions of ALL significant low-ink runs.

                Uses a noise-tolerant threshold (noise_frac × peak) instead of
                requiring exactly-zero ink so that scanner noise / JPEG artefacts
                in whitespace columns do not break gap detection.
                Searches the full 5–95 % of the image (not just middle 40 %).
                """
                border = int(total * border_frac)
                lo, hi = border, total - border
                peak = max(activity[lo:hi], default=1)
                noise_thr = max(1, int(peak * noise_frac))

                centres: list[int] = []
                run_start: int | None = None
                for i in range(lo, hi):
                    if activity[i] <= noise_thr:
                        if run_start is None:
                            run_start = i
                    else:
                        if run_start is not None:
                            run_len = i - run_start
                            if run_len >= min_gap_px:
                                centres.append((run_start + i) // 2)
                            run_start = None
                if run_start is not None:
                    run_len = hi - run_start
                    if run_len >= min_gap_px:
                        centres.append((run_start + hi) // 2)
                return centres

            def _gaps_to_regions_h(
                gap_centres_scan: list[int],
                scan_total: int,
                inv: float,
                orig_total: int,
                orig_perp: int,
                is_vertical: bool,
            ) -> list[dict[str, int]]:
                """
                Convert a list of gap-centre positions (in scan coordinates) to
                region dicts.  Filters out any resulting segment that is narrower
                than 15 % of the total dimension to avoid slivers.
                """
                min_seg = int(scan_total * 0.15)
                sorted_gaps = sorted(gap_centres_scan)

                # Forward pass: drop gaps that create too-narrow leading segments.
                valid: list[int] = []
                prev = 0
                for g in sorted_gaps:
                    if (g - prev) >= min_seg:
                        valid.append(g)
                        prev = g

                # Backward pass: drop trailing gaps that create too-narrow tail segments.
                while valid and (scan_total - valid[-1]) < min_seg:
                    valid.pop()

                if not valid:
                    return []

                orig_splits = [int(g * inv) for g in valid]
                regions: list[dict[str, int]] = []
                prev_pos = 0
                for s in orig_splits:
                    if is_vertical:
                        regions.append({"x": prev_pos, "y": 0, "w": s - prev_pos, "h": orig_perp})
                    else:
                        regions.append({"x": 0, "y": prev_pos, "w": orig_perp, "h": s - prev_pos})
                    prev_pos = s
                if is_vertical:
                    regions.append({"x": prev_pos, "y": 0, "w": orig_total - prev_pos, "h": orig_perp})
                else:
                    regions.append({"x": 0, "y": prev_pos, "w": orig_perp, "h": orig_total - prev_pos})
                return regions

            inv_x = orig_w / float(scan_w)
            inv_y = orig_h / float(scan_h)

            v_centres = _find_all_gap_centres(col_ink, scan_w)
            h_centres = _find_all_gap_centres(row_ink, scan_h)

            v_regions = _gaps_to_regions_h(v_centres, scan_w, inv_x, orig_w, orig_h, is_vertical=True)
            h_regions = _gaps_to_regions_h(h_centres, scan_h, inv_y, orig_h, orig_w, is_vertical=False)

            picked = _pick_force_split_hypothesis(
                v_regions,
                h_regions,
                expected_count=expected_receipt_count,
                image_path=image_path,
            )
            if picked:
                out = _filter_credible_receipt_regions(image_path, picked) or picked
                # Optional column refine is opt-in: default off because single receipts
                # often contain horizontal text gaps that look like stacked slips.
                if refine_columns and len(out) >= 1:
                    refined = _refine_tall_column_regions(image_path, out)
                    if 2 <= len(refined) <= max(len(out) * 3, 2):
                        # Accept only when each child is a substantial share of its parent.
                        ok_children = True
                        for reg in out:
                            kids = [
                                r
                                for r in refined
                                if r["x"] >= reg["x"]
                                and r["y"] >= reg["y"]
                                and r["x"] + r["w"] <= reg["x"] + reg["w"] + 2
                                and r["y"] + r["h"] <= reg["y"] + reg["h"] + 2
                            ]
                            if len(kids) <= 1:
                                continue
                            parent_h = max(1, int(reg["h"]))
                            if any(int(k["h"]) < 0.28 * parent_h for k in kids):
                                ok_children = False
                                break
                        if ok_children:
                            out = refined
                return out

            # Last resort: centre half-split along the longer axis.
            if orig_w >= orig_h:
                split_x = max(int(orig_w * 0.25), min(int(orig_w * 0.75), orig_w // 2))
                fallback = [
                    {"x": 0,       "y": 0, "w": split_x,          "h": orig_h},
                    {"x": split_x, "y": 0, "w": orig_w - split_x, "h": orig_h},
                ]
            else:
                split_y = max(int(orig_h * 0.25), min(int(orig_h * 0.75), orig_h // 2))
                fallback = [
                    {"x": 0, "y": 0,       "w": orig_w, "h": split_y},
                    {"x": 0, "y": split_y, "w": orig_w, "h": orig_h - split_y},
                ]
            return _filter_credible_receipt_regions(image_path, fallback) or fallback
    except Exception:
        return []


def _crop_receipt_regions(image_path: str, regions: list[dict[str, int]]) -> list[str]:
    """
    Crop receipt regions into temporary image files.
    Caller is responsible for cleanup.
    """
    from PIL import Image

    if not regions:
        return []

    cropped_paths: list[str] = []
    with Image.open(image_path) as img:
        for idx, box in enumerate(regions, start=1):
            x, y, w, h = box["x"], box["y"], box["w"], box["h"]
            crop = img.crop((x, y, x + w, y + h))
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".receipt-{idx}.png")
            tmp_path = tmp.name
            tmp.close()
            crop.save(tmp_path, format="PNG")
            cropped_paths.append(tmp_path)

    return cropped_paths


def _detect_multi_receipt_pages(image_paths: list[str]) -> list[int]:
    suspected_pages: list[int] = []
    for idx, path in enumerate(image_paths, start=1):
        regions = _detect_receipt_regions_v2(path)
        if len(regions) > 1:
            suspected_pages.append(idx)
    return suspected_pages


async def _classify_document_layout(
    first_page_path: str,
    ocr_provider_name: str,
    *,
    page_label: str = "First-page",
) -> str:
    """
    Classifies a document page to decide whether OpenCV segmentation should run.

    Returns "invoice"  → structured single document (invoice, PO, multi-page report).
                          Skip OpenCV; parse the whole page / stitched image directly.
    Returns "receipts" → composite image of multiple independent receipts / slips.
                          Run OpenCV segmentation to split into individual items.

    Applies to both single-page and multi-page inputs:
      Single page  → Scenario A (invoice, skip OCR split) vs B (receipts, run split)
      Multi-page   → Scenario C (invoice, stitch+parse) vs D (receipts, per-page split)

    Uses DOCUMENT_LAYOUT_CLASSIFY_MODEL if set, else Settings VLM_MODEL.
    Falls back to "receipts" on any error so the safer per-page path is taken.
    """
    CLASSIFIER_PROMPT = (
        "Analyze the layout of this document image. "
        "Classify it as ONE of the following:\n"
        '1. "invoice": A formal, structured business document — such as a corporate '
        "invoice, purchase order, statement, or official report. "
        "This applies whether the document is one page or multiple pages. "
        "Key signs: a single company letterhead, an itemised table of goods/services, "
        "a total amount, and a company address or signature block at the bottom.\n"
        '2. "receipts": A composite scan containing TWO OR MORE separate, unrelated '
        "receipts, POS slips, or payment vouchers arranged together on a single page.\n"
        'Respond with ONLY one word in lowercase: "invoice" or "receipts".'
    )
    try:
        result = await _ocr_service.recognize(
            first_page_path,
            provider_name=ocr_provider_name,
            model=resolve_layout_classify_model(),
            prompt_override=CLASSIFIER_PROMPT,
            ocr_options={"temperature": 0.0},
        )
        text = (result.text or "").strip().lower()
        classification = "invoice" if "invoice" in text else "receipts"
        logger.info("[Classifier] %s layout → %s (raw: %r)", page_label, classification, text[:80])
        return classification
    except Exception as exc:
        logger.warning("[Classifier] Failed to classify layout, defaulting to 'receipts': %s", exc)
        return "receipts"


def _vertical_stitch_layout(
    image_paths: list[str],
    *,
    log_budget_scale: bool = True,
) -> tuple[list[tuple[int, int]], int, int]:
    """
    Per-page target (w, h) after PIL pixel-budget scaling, plus combined canvas size.
    Must stay in sync with _stitch_pages_vertically geometry.
    """
    import math
    from PIL import Image as _PILImage
    from app.utils.file_converter import _pil_open_pixel_budget

    budget = _pil_open_pixel_budget()
    widths: list[int] = []
    heights: list[int] = []
    for p in image_paths:
        with _PILImage.open(p) as im:
            widths.append(im.width)
            heights.append(im.height)

    max_w = max(widths)
    sum_h = sum(heights)
    scale = 1.0
    if max_w * sum_h > budget:
        scale = math.sqrt(budget / (max_w * sum_h)) * 0.99
        if log_budget_scale:
            logger.info("[Stitch] Scaling pages by %.6f to stay under PIL pixel budget (%s)", scale, budget)

    tw = [max(1, int(w * scale)) for w in widths]
    th = [max(1, int(h * scale)) for h in heights]
    max_width = max(tw)
    total_height = sum(th)
    targets = list(zip(tw, th, strict=True))
    return targets, max_width, total_height


def _stitch_collapses_on_upload(image_paths: list[str]) -> bool:
    """
    True if a vertical stitch of image_paths would be resized by the OCR provider so that
    the shorter side falls below AP_STITCH_UPLOAD_MIN_SHORT_EDGE (unreadable strip), matching
    DeepSeekOcrProvider's max_side scaling.

    When VLM_MAX_SIDE is unset or 0, assume 2000 — the smallest
    max_side in the provider's default PNG profile ladder (see providers.py).
    """
    if not image_paths:
        return False
    try:
        upload_max_side = int(os.getenv("VLM_MAX_SIDE") or "0")
    except ValueError:
        upload_max_side = 0
    if upload_max_side <= 0:
        upload_max_side = 2000
    _targets, stitch_w, stitch_h = _vertical_stitch_layout(image_paths, log_budget_scale=False)
    max_dim = max(stitch_w, stitch_h)
    if max_dim <= 0:
        return False
    if max_dim <= upload_max_side:
        short_edge = min(stitch_w, stitch_h)
    else:
        scale = upload_max_side / max_dim
        wn = int(stitch_w * scale)
        hn = int(stitch_h * scale)
        short_edge = min(wn, hn)
    return short_edge < AP_STITCH_UPLOAD_MIN_SHORT_EDGE


def _stitch_collapses_for_canvas(widths: list[int], heights: list[int]) -> bool:
    if not widths or not heights:
        return False
    try:
        upload_max_side = int(os.getenv("VLM_MAX_SIDE") or "0")
    except ValueError:
        upload_max_side = 0
    if upload_max_side <= 0:
        upload_max_side = 2000
    stitch_w = max(widths)
    stitch_h = sum(heights)
    max_dim = max(stitch_w, stitch_h)
    if max_dim <= 0:
        return False
    if max_dim <= upload_max_side:
        short_edge = min(stitch_w, stitch_h)
    else:
        scale = upload_max_side / max_dim
        short_edge = min(int(stitch_w * scale), int(stitch_h * scale))
    return short_edge < AP_STITCH_UPLOAD_MIN_SHORT_EDGE


def _stitch_pages_vertically(image_paths: list[str]) -> str:
    """
    Stitches multiple page images into a single tall combined image (Scenario C).
    Returns the path to a temp PNG file that the caller is responsible for deleting.
    """
    from PIL import Image as _PILImage

    targets, max_width, total_height = _vertical_stitch_layout(image_paths)

    combined = _PILImage.new("RGB", (max_width, total_height), (255, 255, 255))
    y_offset = 0
    for path, (target_w, target_h) in zip(image_paths, targets, strict=True):
        with _PILImage.open(path) as page:
            im = page.convert("RGB")
            if im.width != target_w or im.height != target_h:
                im = im.resize((target_w, target_h), _PILImage.LANCZOS)
            combined.paste(im, (0, y_offset))
            y_offset += target_h

    tmp = tempfile.NamedTemporaryFile(suffix="_stitched.png", delete=False)
    combined.save(tmp.name, "PNG")
    combined.close()
    logger.info("[Stitch] %d pages → %s (%dx%d px)", len(image_paths), tmp.name, max_width, total_height)
    return tmp.name


def _load_company_context(db: Session, company_id: str, document_type: str) -> dict:
    """Load company profile for AI context. Rules are now handled by Rule Memory (MD files)."""
    profile = db.query(CompanyProfile).filter(CompanyProfile.company_id == company_id).first()
    profile_settings = profile.custom_settings if profile and isinstance(profile.custom_settings, dict) else {}
    fallback_keywords = profile_settings.get("company_name_keywords")

    return {
        "company_id": company_id,
        "profile": {
            "industry": profile.industry if profile else None,
            "accounting_basis": profile.accounting_basis if profile else None,
            "fiscal_year_end": profile.fiscal_year_end if profile else None,
            "company_name": (
                (profile.company_name if profile else None)
                or (profile_settings.get("company_name") if isinstance(profile_settings.get("company_name"), str) else None)
            ),
            "company_name_keywords": (
                profile.company_name_keywords
                if profile and isinstance(profile.company_name_keywords, list)
                else (fallback_keywords if isinstance(fallback_keywords, list) else [])
            ),
            "custom_settings": profile.custom_settings if profile and profile.custom_settings else {},
        },
    }


def _load_rule_memory_for_ocr(db: Session, company_id: str, processing_mode: str) -> str:
    """
    Load the CompanyRuleMemory MD content for the given processing mode.
    Returns the full MD string, or empty string if not found.
    Modes RECON/REPORT (and any unknown mode) fall back to AR for OCR — those
    skills apply to chat/recon/report flows, not document extraction.
    """
    from app.core.processing_mode import normalize_processing_mode

    mode = normalize_processing_mode(processing_mode, "AR")
    if mode not in _OCR_RULE_MEMORY_MODES:
        mode = "AR"  # safe fallback
    try:
        row = db.query(CompanyRuleMemory).filter(
            CompanyRuleMemory.company_id == company_id,
            CompanyRuleMemory.mode == mode,
        ).first()
        if row and row.content and getattr(row, "is_active", True):
            return row.content or ""
        return ""
    except Exception as exc:
        logger.warning("[RuleMemory] Failed to load rule memory for %s/%s: %s", company_id, mode, exc)
        return ""


def _load_profile_summary_for_ocr(db: Session, company_id: str) -> str:
    """
    Return a short 2-line company profile summary for Stage 1 VLM prompt injection.
    Falls back gracefully to empty string.
    """
    try:
        profile = db.query(CompanyProfile).filter(
            CompanyProfile.company_id == company_id
        ).first()
        if not profile:
            return ""
        parts: list[str] = []
        if profile.company_name:
            parts.append(f"Company: {profile.company_name}")
        if profile.industry:
            parts.append(f"Industry: {profile.industry}")
        custom = profile.custom_settings if isinstance(profile.custom_settings, dict) else {}
        currency = custom.get("currency", "HKD")
        parts.append(f"Currency: {currency}")
        return " | ".join(parts) if parts else ""
    except Exception:
        return ""


def _load_exclusion_rules_for_ocr(db: Session, company_id: str) -> list:
    """
    Load all active ExclusionRule rows for this company.
    Returns empty list on any error so the OCR pipeline is never interrupted.
    """
    try:
        from app.models.exclusion_rule import ExclusionRule
        return (
            db.query(ExclusionRule)
            .filter(
                ExclusionRule.company_id == company_id,
                ExclusionRule.is_active == True,  # noqa
            )
            .all()
        )
    except Exception as exc:
        logger.warning("[Exclusion] Failed to load exclusion rules: %s", exc)
        return []


def _inject_trace_meta(ai_payload: dict | None, *, trace_id: str) -> None:
    if not isinstance(ai_payload, dict):
        return
    context_meta = ai_payload.get("context_meta")
    if not isinstance(context_meta, dict):
        context_meta = {}
    context_meta["trace_id"] = trace_id
    ai_payload["context_meta"] = context_meta


def _record_processing_event(
    db: Session,
    *,
    company_id: str,
    trace_id: str,
    filename: str,
    stage: str,
    source: str,
    reason: str,
    outcome: str,
    metadata: dict | None = None,
) -> None:
    db.add(
        OcrCompletionEvent(
            company_id=company_id,
            trace_id=trace_id,
            filename=filename,
            stage=stage,
            source=source,
            metadata_json={
                "trace_id": trace_id,
                "decision_evidence": build_decision_evidence(
                    action=stage,
                    stage="ocr_pipeline",
                    reason=reason,
                    outcome=outcome,
                    source=source,
                    trace_id=trace_id,
                    metadata=metadata or {},
                ),
            },
        )
    )
    db.commit()


@router.post("/ocr/debug")
async def ocr_debug(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
) -> dict:
    """Debug endpoint to test file upload without OCR processing (authenticated)."""
    import cv2

    content = await file.read()
    try:
        assert_upload_size(content)
        assert_file_type(file.filename or "image.jpg", content)
    except ValueError as exc:
        detail = str(exc)
        code = 413 if "maximum size" in detail.lower() else 400
        raise HTTPException(status_code=code, detail=detail) from exc

    suffix = os.path.splitext(file.filename or "image.jpg")[1]
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode="wb") as tmp_file:
        tmp_file.write(content)
        tmp_file.flush()
        os.fsync(tmp_file.fileno())
        tmp_path = tmp_file.name

    try:
        img = cv2.imread(tmp_path)
        return {
            "status": "success",
            "filename": file.filename,
            "content_type": file.content_type,
            "size_bytes": len(content),
            "opencv_read": "success" if img is not None else "failed",
            "image_shape": list(img.shape) if img is not None else None,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "filename": file.filename,
            "content_type": file.content_type,
            "size_bytes": len(content),
        }
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


@router.post("/ocr/test")
async def ocr_test(
    file: UploadFile = File(...),
    processing_mode: Optional[str] = Form("AR"),
    multi_receipt_confirmed: bool = Form(False),
    multi_receipt_acknowledged: bool = Form(False),
    force_process: bool = Form(False),
    ap_vlm_receipt_signal: Optional[str] = Form(None),
    ap_vlm_table_preset: Optional[str] = Form(None),
    company_id: str = Depends(get_current_company_id),
    trace_id: str = Depends(get_trace_id),
    db: Session = Depends(get_db),
) -> dict:
    """
    OCR with AI Enhancement (DeepSeek)
    Performs OCR + Field Filtering + AI Post-Processing
    Supports: Images (JPG, PNG, BMP, TIFF, WEBP) and PDF (single/multi-page)

    Args:
        file: Upload file (image or PDF)
        processing_mode: AR (Receivables) or AP (Payables). Defaults to AR.
    """
    async with long_running_db_work_slot():
        return await ocr_test_core(
            file=file,
            processing_mode=processing_mode or "AR",
            multi_receipt_confirmed=multi_receipt_confirmed,
            multi_receipt_acknowledged=multi_receipt_acknowledged,
            force_process=force_process,
            company_id=company_id,
            trace_id=trace_id,
            db=db,
            ap_vlm_receipt_signal=ap_vlm_receipt_signal,
            ap_vlm_table_preset=ap_vlm_table_preset,
        )


async def ocr_test_core(
    file: UploadFile,
    processing_mode: str,
    multi_receipt_confirmed: bool,
    multi_receipt_acknowledged: bool,
    force_process: bool,
    company_id: str,
    trace_id: str,
    db: Session,
    *,
    background_job_id: str | None = None,
    ap_vlm_model_override: str | None = None,
    ap_force_cross_verify: bool = False,
    ap_vlm_receipt_signal: str | None = None,
    ap_vlm_table_preset: str | None = None,
    workflow_run_id: str | None = None,
    rescan_reasons: list[str] | None = None,
    rescan_note: str | None = None,
    rescan_prior_summary: str | None = None,
    expected_receipt_count: int | None = None,
) -> dict:
    """Core OCR pipeline (shared by /ocr/test and background OCR jobs).

    background_job_id: when set (background job only), OCR stops cooperatively if the job is cancelled.
    workflow_run_id: when set (workflow VLM), OCR stops when the run is hard-stopped.
    ap_vlm_model_override: when set and processing_mode is AP, use this model id instead of AP_VLM_MODEL for the primary pass.
    ap_force_cross_verify: when True, run AP cross-VLM merge even if AP_AUTO_CROSS_VERIFY_ENABLED is off (manual Double check).
    ap_vlm_receipt_signal / ap_vlm_table_preset: optional AP user hints from the workspace composer (guess/single_per_page/…).
    """

    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    
    # Get file extension
    suffix = os.path.splitext(file.filename)[1].lower()
    supported_images = [".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"]
    supported_pdf = [".pdf"]
    
    is_pdf = suffix in supported_pdf
    is_image = suffix in supported_images
    
    if not is_pdf and not is_image:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file format: {suffix}. Supported: jpg, jpeg, png, bmp, tiff, webp, pdf"
        )
    
    if is_pdf and not PDF_SUPPORT:
        raise HTTPException(
            status_code=500,
            detail="PDF support not available. Please install PyMuPDF"
        )
    
    # Read file content
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        assert_upload_size(content)
        assert_file_type(file.filename or "upload.bin", content)
    except ValueError as exc:
        detail = str(exc)
        code = 413 if "maximum size" in detail.lower() else 400
        raise HTTPException(status_code=code, detail=detail) from exc

    # Monthly cost cap check before starting heavy processing
    _cost_ok, _cost_msg = check_monthly_cost(db, company_id)
    if not _cost_ok:
        raise HTTPException(status_code=429, detail=_cost_msg)

    _sig_raw = ap_vlm_receipt_signal.strip().lower() if isinstance(ap_vlm_receipt_signal, str) else ""
    _tp_raw = ap_vlm_table_preset.strip().lower() if isinstance(ap_vlm_table_preset, str) else ""
    _ap_rs = _sig_raw if _sig_raw in AP_VLM_RECEIPT_SIGNAL_VALUES else "guess"
    _ap_tp = _tp_raw if _tp_raw in AP_VLM_TABLE_PRESET_VALUES else "default"

    if processing_mode == "AP" and _ap_rs == "multi_per_page":
        multi_receipt_confirmed = True

    # Per-company concurrent OCR cap (Redis across instances, or local semaphore).
    _ocr_cm = company_ocr_concurrency(company_id)
    await _ocr_cm.__aenter__()
    _cv_reset = _ap_cross_verify_force_cv.set(ap_force_cross_verify)
    _wf_reset = _workflow_run_id_cv.set(workflow_run_id) if workflow_run_id else None

    # Save to temporary file with proper flushing
    tmp_path = None
    image_paths = []
    process_path = None
    try:
        used_pdf_text_extraction = False
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode='wb') as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()  # Ensure content is written to disk
            os.fsync(tmp_file.fileno())  # Force write to disk
            tmp_path = tmp_file.name
        
        # Verify file exists and is readable
        if not os.path.exists(tmp_path):
            raise HTTPException(status_code=500, detail="Failed to save uploaded file")
        
        file_size = os.path.getsize(tmp_path)
        if file_size == 0:
            raise HTTPException(status_code=400, detail="Saved file is empty")

        _raise_if_bg_job_cancelled(background_job_id)

        logger.info("="*60)
        logger.info(f"[NEW REQUEST] {file.filename}")
        logger.info(f"   Type: {'PDF' if is_pdf else 'Image'}")
        logger.info(f"   Size: {file_size:,} bytes ({file_size/1024:.1f} KB)")
        logger.info(f"   Mode: {processing_mode}")
        # Model IDs: AR/AP from env; BANK uses BANK_VLM_MODEL; others use VLM_MODEL default.
        # Registry key stays the legacy alias; the real VLM id is passed via set_model().
        if processing_mode == "AR":
            ocr_provider_name = AR_OCR_MODEL
            ocr_model_override = AR_OCR_MODEL
            logger.info("   Model: AR_OCR_MODEL=%s", AR_OCR_MODEL)
        elif processing_mode == "AP":
            ocr_provider_name = settings.ocr_provider
            _ap_manual = (ap_vlm_model_override or "").strip()
            ocr_model_override = _ap_manual or AP_VLM_MODEL
            if _ap_manual:
                logger.info(
                    "   Model: AP_VLM override=%s (base AP_VLM_MODEL=%s, provider alias=%s)",
                    ocr_model_override,
                    AP_VLM_MODEL,
                    ocr_provider_name,
                )
            else:
                logger.info(
                    "   Model: AP_VLM_MODEL=%s (provider alias=%s)",
                    AP_VLM_MODEL,
                    ocr_provider_name,
                )
        else:
            ocr_provider_name = settings.ocr_provider
            ocr_model_override = (
                BANK_VLM_MODEL if processing_mode == "BANK"
                else settings.vlm_model
            )
            logger.info("   Model: %s (mode=%s)", ocr_model_override, processing_mode)
        # AR and AP both use the same plain-text document-parsing prompt.
        # AR_AP_HTML_OCR_PROMPT is no longer used; the structured JSON extraction
        # (_extract_ap_ai_fields_for_page) handles both modes directly from the image.
        ocr_prompt_override = (
            BANK_TABLE_PARSING_PROMPT
            if processing_mode == "BANK"
            else (AP_MULTI_RECEIPT_DOCUMENT_PARSING_PROMPT if processing_mode in ("AR", "AP") else None)
        )
        _expected_count, _count_source, _count_strength = resolve_expected_receipt_count(
            explicit=expected_receipt_count,
            note=rescan_note,
        )
        _prefer_denser_split = "missed_receipts" in validate_rescan_reasons(rescan_reasons or [])
        _rescan_block = build_rescan_prompt_block(
            reasons=rescan_reasons,
            note=rescan_note,
            prior_summary=rescan_prior_summary,
            expected_receipt_count=_expected_count,
        )
        _rescan_supplement = _rescan_block or None
        _multi_receipt_kwargs = {
            "rescan_supplement": _rescan_supplement,
            "expected_receipt_count": _expected_count,
            "count_assertion_strength": _count_strength,
            "prefer_denser_split": _prefer_denser_split,
        }
        if _expected_count is not None:
            logger.info(
                "   [M-VDU] expected_receipt_count=%s source=%s strength=%s prefer_denser=%s",
                _expected_count,
                _count_source,
                _count_strength,
                _prefer_denser_split,
            )
        # Stage 1 enhancement: inject AI Behaviour Instructions from rule memory into the VLM prompt
        _stage1_rule_md = _load_rule_memory_for_ocr(db, company_id, processing_mode)
        _stage1_hints = _extract_ai_instructions(_stage1_rule_md)
        # Always inject a short company profile summary (name, industry, currency)
        _profile_summary = _load_profile_summary_for_ocr(db, company_id)
        if ocr_prompt_override:
            if _profile_summary:
                ocr_prompt_override = (
                    f"[COMPANY CONTEXT: {_profile_summary}]\n\n"
                    + ocr_prompt_override
                )
            if _stage1_hints:
                ocr_prompt_override = (
                    ocr_prompt_override
                    + "\n\n[COMPANY DOCUMENT HINTS]\n"
                    + _stage1_hints
                    + "\n\nApply the above hints when reading known vendor documents.\n"
                )
            if processing_mode == "AP":
                _ux_hints: list[str] = []
                if _ap_rs != "guess":
                    _hint_line = _AP_VLM_RECEIPT_SIGNAL_HINTS.get(_ap_rs)
                    if _hint_line:
                        _ux_hints.append(_hint_line)
                if _ap_tp == "ap_table":
                    _ux_hints.append(AP_VLM_AP_TABLE_COLUMN_HINT)
                if _ux_hints:
                    ocr_prompt_override = (
                        ocr_prompt_override
                        + "\n\n[USER UPLOAD OPTIONS]\n"
                        + "\n".join(_ux_hints)
                    )
            if _rescan_block:
                ocr_prompt_override = ocr_prompt_override + "\n\n" + _rescan_block
        logger.info(f"   Temp file: {tmp_path}")
        logger.info("="*60)

        # ── Document Gate (Stage 0) ────────────────────────────────────────────
        # Only run for AR/AP modes — BANK is always transactional.
        # force_process=True skips the gate (user explicitly chose "Process anyway").
        _gate_ocr_text: str | None = None  # populated below when first OCR text available
        cheque_router_probe: dict[str, Any] | None = None

        # Handle PDF: Try text extraction first, then OCR
        if is_pdf:
            min_chars = int(os.getenv("PDF_TEXT_EXTRACTION_MIN_CHARS", "200"))
            pdf_text_pages = _extract_pdf_text_pages(tmp_path)
            total_text = "".join(page["text"] for page in pdf_text_pages if page["text"])
            # BANK/AR/AP modes must use OCR path:
            # - BANK requires the table-parsing prompt for structured bank statement output
            # - AR/AP use the structured JSON extraction pipeline (_extract_ap_ai_fields_for_page)
            #   which requires image input; PDF text extraction bypasses this pipeline
            if processing_mode in ("BANK", "AR", "AP"):
                logger.info(
                    "[STEP 1] %s mode: skip PDF text extraction and force OCR path.",
                    processing_mode,
                )
            elif len(total_text) >= min_chars:
                logger.info(
                    "[STEP 1] Using PDF text extraction (%s chars) instead of OCR.",
                    len(total_text),
                )
                used_pdf_text_extraction = True
                if len(pdf_text_pages) > 1:
                    all_pages_results = []

                    for page in pdf_text_pages:
                        _raise_if_bg_job_cancelled(background_job_id)
                        page_num = page["page"]
                        page_text = page["text"]
                        ocr_result = _build_text_ocr_result(page_text, "pdf_text")

                        logger.info(f"   [FIELDS] Extracting from page {page_num} (text)...")
                        filtered_result = _filtering_pipeline.filter_and_extract(ocr_result)
                        logger.info(f"   [FIELDS] Extracted {len(filtered_result['fields'])} fields")
                        _record_processing_event(
                            db,
                            company_id=company_id,
                            trace_id=trace_id,
                            filename=file.filename or "",
                            stage="ocr_complete",
                            source="pdf_text_extraction",
                            reason="page_text_extracted",
                            outcome="completed",
                            metadata={"page": page_num, "mode": processing_mode},
                        )

                        ai_enhanced_fields = None
                        if _ai_processor.api_key:
                            try:
                                logger.info(
                                    f"   [AI] Running enhancement for page {page_num} (mode: {processing_mode})..."
                                )
                                detected_type = _document_type_for_enhancement(
                                    processing_mode, ocr_result.text, page_num=page_num
                                )
                                company_context = _load_company_context(db, company_id, detected_type)
                                logger.info(f"   [AI] Detected document type: {detected_type}")
                                ai_enhanced = await _ai_processor.enhance_ocr_result(
                                    ocr_result,
                                    document_type=detected_type,
                                    processing_mode=processing_mode,
                                    metadata={
                                        "company_context": company_context,
                                        "multi_receipt_confirmed": multi_receipt_confirmed,
                                        "page_num": page_num,
                                    },
                                )
                                ai_enhanced_fields = ai_enhanced
                                if isinstance(ai_enhanced_fields, dict):
                                    _inject_trace_meta(ai_enhanced_fields, trace_id=trace_id)
                                    context_meta = (
                                        ai_enhanced_fields.get("context_meta")
                                        if isinstance(ai_enhanced_fields.get("context_meta"), dict)
                                        else {}
                                    )
                                    context_meta["rule_memory_mode"] = processing_mode
                                    ai_enhanced_fields["context_meta"] = context_meta
                                    # Stage 2: apply MD rule memory (3-tier priority, conflict flagging)
                                    if isinstance(ai_enhanced_fields.get("transactions"), list):
                                        _rule_md = _load_rule_memory_for_ocr(db, company_id, processing_mode)
                                        ai_enhanced_fields["transactions"] = _apply_rules_from_memory(
                                            ai_enhanced_fields["transactions"],
                                            _rule_md,
                                            ocr_result.text,
                                        )
                                        # Stage 2b: apply exclusion rules (flag needs_manual_review)
                                        _excl_rules = _load_exclusion_rules_for_ocr(db, company_id)
                                        if _excl_rules:
                                            ai_enhanced_fields["transactions"] = _apply_exclusions(
                                                ai_enhanced_fields["transactions"],
                                                _excl_rules,
                                                ocr_result.text,
                                                processing_mode,
                                                db,
                                            )
                                _record_processing_event(
                                    db,
                                    company_id=company_id,
                                    trace_id=trace_id,
                                    filename=file.filename or "",
                                    stage="ai_complete",
                                    source="ocr_test_pdf_text_page",
                                    reason="ai_post_process_success",
                                    outcome="completed",
                                    metadata={"page": page_num, "mode": processing_mode},
                                )
                                logger.info(f"   [AI] Enhancement complete for page {page_num}")
                            except Exception as e:
                                logger.warning(
                                    f"   [WARN] AI enhancement failed for page {page_num}: {str(e)}"
                                )

                        all_pages_results.append({
                            "page": page_num,
                            "text": ocr_result.text,
                            "lines_count": len(ocr_result.lines),
                            "extracted_fields": filtered_result["fields"],
                            "field_confidence": filtered_result["overall_confidence"],
                            "ai_enhanced": ai_enhanced_fields
                        })

                    return {
                        "trace_id": trace_id,
                        "filename": file.filename,
                        "document_type": "multi_page_pdf",
                        "total_pages": len(pdf_text_pages),
                        "pages": all_pages_results,
                        "provider": "pdf_text_extraction",
                        "processing_mode": processing_mode,
                        "processing_steps": {
                            "pdf_text_extraction": "completed",
                            "ai_enhancement": "completed" if _ai_processor.api_key else "skipped"
                        }
                    }
                else:
                    single_text = pdf_text_pages[0]["text"] if pdf_text_pages else ""
                    ocr_result = _build_text_ocr_result(single_text, "pdf_text")
                    process_path = None
            if not used_pdf_text_extraction:
                logger.info("[STEP 1] Converting PDF to images for OCR...")
                n_pages = await asyncio.to_thread(pdf_document_page_count, tmp_path)
                image_paths: List[str] = []
                for pnum in range(1, n_pages + 1):
                    _raise_if_bg_job_cancelled(background_job_id)
                    img_path = await asyncio.to_thread(
                        convert_one_pdf_page_to_temp_png, tmp_path, pnum
                    )
                    image_paths.append(img_path)
                logger.info(f"PDF has {len(image_paths)} page(s)")
                _raise_if_bg_job_cancelled(background_job_id)
                # ── 4-Scenario Decision Tree (AP & AR, no user confirmation needed) ────
                if len(image_paths) > 1:
                    # Multi-page PDF: classify layout to distinguish Scenario C vs D.
                    if processing_mode in ("AR", "AP"):
                        _raise_if_bg_job_cancelled(background_job_id)
                        # single_span_pages: one logical document across pages → invoice + stitch (Scenario C).
                        # single_per_page: at most one slip per PDF page → parallel per-page (Scenario D), no stitch.
                        _ap_mp_skip_for_span = processing_mode == "AP" and _ap_rs == "single_span_pages"
                        _ap_mp_skip_for_per_page = processing_mode == "AP" and _ap_rs == "single_per_page"
                        if is_vlm_detection_backend():
                            doc_class = "receipts"
                            logger.info(
                                "[ROUTER] Settings VLM Detect: multi-page PDF → per-page Detect "
                                "(invoice classifier skipped)",
                            )
                        elif _ap_mp_skip_for_span:
                            doc_class = "invoice"
                            logger.info(
                                "[AP] receipt_signal=%s → skip multi-page PDF classifier; "
                                "invoice routing (document may span pages)",
                                _ap_rs,
                            )
                        elif _ap_mp_skip_for_per_page:
                            doc_class = "receipts"
                            logger.info(
                                "[AP] receipt_signal=single_per_page → skip multi-page PDF classifier; "
                                "per-page parallel routing (Scenario D)",
                            )
                        else:
                            cv_shortcut = os.getenv("AP_LAYOUT_CV_SHORTCUT_ENABLED", "").lower() in (
                                "1",
                                "true",
                                "yes",
                            )
                            if cv_shortcut:
                                regions_fp = await asyncio.to_thread(
                                    _detect_receipt_regions_v2, image_paths[0]
                                )
                                if len(regions_fp) == 1:
                                    doc_class = "invoice"
                                    logger.info(
                                        "[%s] CV layout shortcut: page 1 has single OpenCV region → invoice",
                                        processing_mode,
                                    )
                                else:
                                    doc_class = await _classify_document_layout(
                                        image_paths[0], ocr_provider_name
                                    )
                                    logger.info(
                                        "[%s] Multi-page document (%d pages) → AI classifier: %s",
                                        processing_mode,
                                        len(image_paths),
                                        doc_class,
                                    )
                            else:
                                doc_class = await _classify_document_layout(
                                    image_paths[0], ocr_provider_name
                                )
                                logger.info(
                                    "[%s] Multi-page document (%d pages) → AI classifier: %s",
                                    processing_mode,
                                    len(image_paths),
                                    doc_class,
                                )
                            if (
                                doc_class == "invoice"
                                and len(image_paths) >= AP_LAYOUT_LAST_PAGE_MIN_PAGES
                            ):
                                _raise_if_bg_job_cancelled(background_job_id)
                                doc_last = await _classify_document_layout(
                                    image_paths[-1],
                                    ocr_provider_name,
                                    page_label="Last-page",
                                )
                                if doc_last == "receipts":
                                    doc_class = "receipts"
                                    logger.info(
                                        "[Classifier] Last-page layout → receipts; "
                                        "routing as receipts for multi-page PDF.",
                                    )
                    else:
                        doc_class = "receipts"  # non-AR/AP modes: always batch per page

                    if processing_mode in ("AR", "AP"):
                        stitch_collapsed = _stitch_collapses_on_upload(image_paths)
                    else:
                        stitch_collapsed = False
                    use_full_stitch = doc_class == "invoice" and not stitch_collapsed

                    if use_full_stitch:
                        # ── Scenario C: multi-page single invoice ──────────────────────────
                        # Stitch all pages into one tall image and parse directly.
                        # OpenCV segmentation is intentionally skipped here: the classifier
                        # already confirmed this is one logical document. Running OpenCV on
                        # the stitched image risks treating the page-join seam as a document
                        # boundary and splitting a single invoice into fragments.
                        logger.info(
                            "[%s] Scenario C: stitching %d pages into single image (no OpenCV)...",
                            processing_mode, len(image_paths),
                        )
                        _raise_if_bg_job_cancelled(background_job_id)
                        try:
                            stitched_path = _stitch_pages_vertically(image_paths)
                            process_path = stitched_path
                        except Exception:
                            # If stitching fails, fall back to first page only.
                            logger.warning("[%s] Stitched processing failed; falling back to first page.", processing_mode)
                            process_path = image_paths[0]
                        # Do NOT delete process_path here — it is used as process_path below.

                    else:
                        if doc_class == "invoice" and stitch_collapsed:
                            logger.info(
                                "[%s] Scenario C skipped: stitched image would collapse on "
                                "VLM upload (min edge after max_side resize < %d px); "
                                "using per-page processing (one extraction per page).",
                                processing_mode,
                                AP_STITCH_UPLOAD_MIN_SHORT_EDGE,
                            )
                        # ── Scenario D: batch scan — each page processed in parallel ─────
                        logger.info(
                            "[%s] Scenario D: parallel processing %d pages (max 3 concurrent)...",
                            processing_mode, len(image_paths),
                        )

                        # Pre-load shared DB data once — avoids repeated DB round-trips
                        # and allows each page coroutine to run without touching db for reads.
                        _rule_md_pre = _load_rule_memory_for_ocr(db, company_id, processing_mode)
                        _excl_rules_pre = _load_exclusion_rules_for_ocr(db, company_id)

                        # Semaphore caps concurrent API calls to 3 to avoid upstream rate limits.
                        _page_sem = asyncio.Semaphore(3)

                        async def _process_one_page(page_num: int, img_path: str) -> dict:
                            """
                            Process a single PDF page and return a result dict.
                            DB write events are collected in '_events' and flushed after gather()
                            so the shared db Session is never touched concurrently.
                            """
                            pending_events: list[dict] = []

                            async with _page_sem:
                                _raise_if_bg_job_cancelled(background_job_id)
                                logger.info(
                                    "[PAGE %d/%d] Processing...", page_num, len(image_paths)
                                )

                                # ── AR / AP: receipt-region detection + structured extraction ──
                                if processing_mode in ("AR", "AP"):
                                    if is_vlm_detection_backend():
                                        logger.info(
                                            "   [%s page %s] Settings VLM Detect → native crop → "
                                            "receipt_instance OCR",
                                            processing_mode,
                                            page_num,
                                        )
                                        multi_result = await _run_ap_multi_receipt_ocr_from_image(
                                            img_path,
                                            trace_id=trace_id,
                                            filename=file.filename or "",
                                            ocr_provider_name=ocr_provider_name,
                                            ocr_model_override=ocr_model_override,
                                            ocr_prompt_override=ocr_prompt_override,
                                            processing_mode=processing_mode,
                                            confirmed=True,
                                            pdf_page_num=page_num,
                                            background_job_id=background_job_id,
                                            **_multi_receipt_kwargs,
                                        )
                                        pending_events.append({
                                            "company_id": company_id,
                                            "trace_id": trace_id,
                                            "filename": file.filename or "",
                                            "stage": "ocr_complete",
                                            "source": "ocr_test_pdf_image_page",
                                            "reason": (
                                                "vlm_split_review"
                                                if multi_result and multi_result.get("needs_split_review")
                                                else "vlm_receipt_instances_completed"
                                            ),
                                            "outcome": "completed",
                                            "metadata": {
                                                "page": page_num,
                                                "mode": processing_mode,
                                                "instances": len((multi_result or {}).get("pages") or []),
                                            },
                                        })
                                        if multi_result and multi_result.get("needs_split_review"):
                                            review_page = _vlm_split_review_page(
                                                page_num,
                                                message=str(multi_result.get("message") or ""),
                                            )
                                            review_page["_events"] = pending_events
                                            return review_page
                                        sub_pages = [
                                            _public_ap_receipt_page(sub, page_num)
                                            for sub in (multi_result or {}).get("pages", [])
                                            if isinstance(sub, dict)
                                        ]
                                        return {
                                            "page": page_num,
                                            "_multi": True,
                                            "_pages": sub_pages,
                                            "_events": pending_events,
                                        }

                                    receipt_regions = await asyncio.to_thread(
                                        _detect_receipt_regions_v2, img_path
                                    )
                                    logger.info(
                                        "   [%s page %s] %s receipt region(s) detected.",
                                        processing_mode, page_num, len(receipt_regions),
                                    )

                                    if len(receipt_regions) > 1:
                                        logger.info(
                                            "   [%s page %s] Running multi-receipt OCR (%s regions)...",
                                            processing_mode, page_num, len(receipt_regions),
                                        )
                                        multi_result = await _run_ap_multi_receipt_ocr_from_image(
                                            img_path,
                                            trace_id=trace_id,
                                            filename=file.filename or "",
                                            ocr_provider_name=ocr_provider_name,
                                            ocr_model_override=ocr_model_override,
                                            ocr_prompt_override=ocr_prompt_override,
                                            processing_mode=processing_mode,
                                            confirmed=multi_receipt_confirmed,
                                            pdf_page_num=page_num,
                                            background_job_id=background_job_id,
                                            **_multi_receipt_kwargs,
                                        )
                                        if multi_result is not None:
                                            pending_events.append({
                                                "company_id": company_id,
                                                "trace_id": trace_id,
                                                "filename": file.filename or "",
                                                "stage": "ocr_complete",
                                                "source": "ocr_test_pdf_image_page",
                                                "reason": "multi_receipt_ocr_completed",
                                                "outcome": "completed",
                                                "metadata": {
                                                    "page": page_num,
                                                    "mode": processing_mode,
                                                    "regions": len(receipt_regions),
                                                },
                                            })
                                            sub_pages = [
                                                _public_ap_receipt_page(sub, page_num)
                                                for sub in multi_result.get("pages", [])
                                                if isinstance(sub, dict)
                                            ]
                                            return {
                                                "page": page_num,
                                                "_multi": True,
                                                "_pages": sub_pages,
                                                "_events": pending_events,
                                            }

                                    # Single receipt on this page: OCR + structured extraction.
                                    _mode_label = processing_mode  # "AP" or "AR"
                                    logger.info(
                                        "   [%s page %s] Single-receipt OCR with %s...",
                                        _mode_label, page_num, ocr_provider_name,
                                    )
                                    ocr_result = await _ocr_service.recognize(
                                        img_path,
                                        provider_name=ocr_provider_name,
                                        model=ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL,
                                        prompt_override=ocr_prompt_override or AP_MULTI_RECEIPT_DOCUMENT_PARSING_PROMPT,
                                    )
                                    filtered_result = _filtering_pipeline.filter_and_extract(ocr_result)
                                    pending_events.append({
                                        "company_id": company_id,
                                        "trace_id": trace_id,
                                        "filename": file.filename or "",
                                        "stage": "ocr_complete",
                                        "source": "ocr_test_pdf_image_page",
                                        "reason": "page_ocr_completed",
                                        "outcome": "completed",
                                        "metadata": {"page": page_num, "mode": _mode_label},
                                    })
                                    ai_enhanced_fields = await _extract_ar_ap_ai_fields_routed(
                                        ocr_text=ocr_result.text,
                                        img_path=img_path,
                                        page_num=page_num,
                                        ocr_provider_name=ocr_provider_name,
                                        ocr_model_override=ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL,
                                        processing_mode=_mode_label,
                                        ocr_lines=ocr_result.lines,
                                        rescan_supplement=_rescan_supplement,
                                    )
                                    ai_enhanced_fields = await _ap_apply_cross_vlm_merge_if_configured(
                                        processing_mode=_mode_label,
                                        primary_model=ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL,
                                        ai_primary=ai_enhanced_fields,
                                        ocr_text=ocr_result.text,
                                        img_path=img_path,
                                        page_num=page_num,
                                        ocr_provider_name=ocr_provider_name,
                                        image_options=None,
                                        ocr_lines=ocr_result.lines,
                                        cheque_probe=None,
                                        rescan_supplement=_rescan_supplement,
                                    )
                                    return {
                                        "page": page_num,
                                        "text": ocr_result.text,
                                        "lines_count": len(ocr_result.lines),
                                        "extracted_fields": filtered_result["fields"],
                                        "field_confidence": filtered_result["overall_confidence"],
                                        "ai_enhanced": ai_enhanced_fields,
                                        "_events": pending_events,
                                    }

                                # ── BANK / other modes: OCR → DeepSeek AI ─────────────────────
                                logger.info(
                                    "   [OCR] Running on page %d using %s...",
                                    page_num, ocr_provider_name,
                                )
                                ocr_result = await _ocr_service.recognize(
                                    img_path,
                                    provider_name=ocr_provider_name,
                                    model=ocr_model_override,
                                    prompt_override=ocr_prompt_override,
                                )
                                logger.info(
                                    "   [OCR] Complete: %d lines detected", len(ocr_result.lines)
                                )
                                filtered_result = _filtering_pipeline.filter_and_extract(ocr_result)
                                pending_events.append({
                                    "company_id": company_id,
                                    "trace_id": trace_id,
                                    "filename": file.filename or "",
                                    "stage": "ocr_complete",
                                    "source": "ocr_test_pdf_image_page",
                                    "reason": "page_ocr_completed",
                                    "outcome": "completed",
                                    "metadata": {"page": page_num, "mode": processing_mode},
                                })
                                ai_enhanced_fields = None
                                if _ai_processor.api_key:
                                    try:
                                        detected_type = _document_type_for_enhancement(
                                            processing_mode, ocr_result.text, page_num=page_num
                                        )
                                        # Use pre-loaded company context key for BANK/other modes
                                        company_context = _load_company_context(
                                            db, company_id, detected_type
                                        )
                                        ai_enhanced = await _ai_processor.enhance_ocr_result(
                                            ocr_result,
                                            document_type=detected_type,
                                            processing_mode=processing_mode,
                                            metadata={
                                                "company_context": company_context,
                                                "page_num": page_num,
                                            },
                                        )
                                        ai_enhanced_fields = ai_enhanced
                                        if isinstance(ai_enhanced_fields, dict):
                                            _inject_trace_meta(
                                                ai_enhanced_fields, trace_id=trace_id
                                            )
                                            ctx = ai_enhanced_fields.get("context_meta") or {}
                                            ctx["rule_memory_mode"] = processing_mode
                                            ai_enhanced_fields["context_meta"] = ctx
                                            if isinstance(
                                                ai_enhanced_fields.get("transactions"), list
                                            ):
                                                # Use pre-loaded rule memory (no extra DB call)
                                                ai_enhanced_fields["transactions"] = (
                                                    _apply_rules_from_memory(
                                                        ai_enhanced_fields["transactions"],
                                                        _rule_md_pre,
                                                        ocr_result.text,
                                                    )
                                                )
                                                if _excl_rules_pre:
                                                    ai_enhanced_fields["transactions"] = (
                                                        _apply_exclusions(
                                                            ai_enhanced_fields["transactions"],
                                                            _excl_rules_pre,
                                                            ocr_result.text,
                                                            processing_mode,
                                                            db,
                                                        )
                                                    )
                                        pending_events.append({
                                            "company_id": company_id,
                                            "trace_id": trace_id,
                                            "filename": file.filename or "",
                                            "stage": "ai_complete",
                                            "source": "ocr_test_pdf_image_page",
                                            "reason": "ai_post_process_success",
                                            "outcome": "completed",
                                            "metadata": {
                                                "page": page_num,
                                                "mode": processing_mode,
                                            },
                                        })
                                    except Exception as exc:
                                        logger.warning(
                                            "   [WARN] AI enhancement failed for page %d: %s",
                                            page_num, exc,
                                        )
                                return {
                                    "page": page_num,
                                    "text": ocr_result.text,
                                    "lines_count": len(ocr_result.lines),
                                    "extracted_fields": filtered_result["fields"],
                                    "field_confidence": filtered_result["overall_confidence"],
                                    "ai_enhanced": ai_enhanced_fields,
                                    "_events": pending_events,
                                }

                        # ── Scenario D queue workers with early-stop policy ───────────────
                        n_pdf_pages = len(image_paths)
                        worker_count = min(AP_LAYOUT_MAX_PAGE_CONCURRENCY, n_pdf_pages)
                        page_queue: asyncio.Queue[tuple[int, str, float] | None] = asyncio.Queue()
                        result_queue: asyncio.Queue[tuple[int, Any, int, int]] = asyncio.Queue()

                        async def _scenario_d_worker() -> None:
                            while True:
                                item = await page_queue.get()
                                if item is None:
                                    page_queue.task_done()
                                    return
                                page_num, img_path, enqueue_ts = item
                                wait_ms = int((time.perf_counter() - enqueue_ts) * 1000)
                                started = time.perf_counter()
                                try:
                                    out = await _process_one_page(page_num, img_path)
                                except asyncio.CancelledError:
                                    if background_job_id and background_job_cancelled(
                                        background_job_id
                                    ):
                                        out = OcrBackgroundJobCancelled()
                                    else:
                                        raise
                                except Exception as exc:  # noqa: BLE001
                                    out = exc
                                proc_ms = int((time.perf_counter() - started) * 1000)
                                await result_queue.put((page_num, out, wait_ms, proc_ms))
                                page_queue.task_done()

                        workers = [asyncio.create_task(_scenario_d_worker()) for _ in range(worker_count)]
                        next_idx = 0
                        inflight = 0
                        terminated_reason: str | None = None
                        observed_pages = 0
                        failed_pages = 0
                        consecutive_failures = 0
                        saw_rate_limit = False
                        per_page_result: dict[int, Any] = {}
                        queue_wait_ms_total = 0
                        proc_ms_total = 0

                        try:
                            while next_idx < n_pdf_pages and inflight < worker_count:
                                next_idx += 1
                                await page_queue.put((next_idx, image_paths[next_idx - 1], time.perf_counter()))
                                inflight += 1

                            while inflight > 0:
                                page_num, page_out, wait_ms, proc_ms = await result_queue.get()
                                inflight -= 1
                                queue_wait_ms_total += wait_ms
                                proc_ms_total += proc_ms
                                observed_pages += 1
                                per_page_result[page_num] = page_out
                                if isinstance(page_out, OcrBackgroundJobCancelled):
                                    raise page_out
                                page_failed = isinstance(page_out, BaseException)
                                if page_failed:
                                    failed_pages += 1
                                    consecutive_failures += 1
                                    if _classify_ocr_error_code(page_out) == "VLM_RATE_LIMIT":
                                        saw_rate_limit = True
                                else:
                                    consecutive_failures = 0
                                if terminated_reason is None:
                                    terminated_reason = _scenario_d_termination_reason(
                                        consecutive_failures=consecutive_failures,
                                        failed_pages=failed_pages,
                                        observed_pages=observed_pages,
                                        saw_rate_limit=saw_rate_limit,
                                    )
                                # Persist a running snapshot so frontend can show progressive rows.
                                partial_pages: list[dict[str, Any]] = []
                                for _pn in range(1, next_idx + 1):
                                    _partial = per_page_result.get(_pn)
                                    if _partial is None:
                                        continue
                                    if isinstance(_partial, OcrBackgroundJobCancelled):
                                        continue
                                    if isinstance(_partial, BaseException):
                                        partial_pages.append(_build_error_page_row(_pn, _partial))
                                        continue
                                    if not isinstance(_partial, dict):
                                        continue
                                    _partial = dict(_partial)
                                    if _partial.pop("_multi", False):
                                        for _sp in _partial.get("_pages", []) or []:
                                            if isinstance(_sp, dict):
                                                _sp = dict(_sp)
                                                _sp.setdefault("status", "success")
                                                partial_pages.append(_sp)
                                    else:
                                        _partial.pop("_pages", None)
                                        _partial.pop("_events", None)
                                        _partial.setdefault("status", "success")
                                        partial_pages.append(_partial)
                                if partial_pages:
                                    partial_outcome = recompute_ocr_job_outcome_from_pages(partial_pages)
                                    _persist_background_job_partial_result(
                                        job_id=background_job_id,
                                        result_json={
                                            "trace_id": trace_id,
                                            "filename": file.filename,
                                            "document_type": "multi_page_pdf",
                                            "total_pages": len(image_paths),
                                            "pages": partial_pages,
                                            "ocr_job_outcome": partial_outcome,
                                            "terminated_reason": None,
                                            "provider": "multi_page_processor",
                                            "processing_mode": processing_mode,
                                            "processing_steps": {
                                                "pdf_conversion": "completed",
                                                "pages_processed": len(partial_pages),
                                                "ai_enhancement": "completed" if _ai_processor.api_key else "skipped",
                                            },
                                        },
                                        progress_percent=25 + int((observed_pages / max(n_pdf_pages, 1)) * 60),
                                        progress_label="OCR 處理中",
                                    )
                                if terminated_reason is None and next_idx < n_pdf_pages:
                                    next_idx += 1
                                    await page_queue.put(
                                        (next_idx, image_paths[next_idx - 1], time.perf_counter())
                                    )
                                    inflight += 1
                        finally:
                            for _w in workers:
                                await page_queue.put(None)
                            await page_queue.join()
                            await asyncio.gather(*workers, return_exceptions=True)

                        merged_pages: list[dict[str, Any]] = []
                        for _page_num in range(1, next_idx + 1):
                            _r = per_page_result.get(_page_num)
                            if isinstance(_r, OcrBackgroundJobCancelled):
                                raise _r
                            if isinstance(_r, BaseException):
                                logger.warning(
                                    "[Scenario D] Page %s failed during parallel processing: %s",
                                    _page_num,
                                    _r,
                                )
                                merged_pages.append(_build_error_page_row(_page_num, _r))
                                continue
                            if not isinstance(_r, dict):
                                merged_pages.append(
                                    _build_error_page_row(
                                        _page_num,
                                        RuntimeError("PAGE_PROCESSING_FAILED: missing page result"),
                                    )
                                )
                                continue
                            for _ev in _r.pop("_events", []):
                                _record_processing_event(db, **_ev)
                            if _r.pop("_multi", False):
                                for _sp in _r.get("_pages", []) or []:
                                    if isinstance(_sp, dict):
                                        _sp.setdefault("status", "success")
                                        merged_pages.append(_sp)
                                _r.pop("_pages", None)
                            else:
                                _r.pop("_pages", None)
                                _r.setdefault("status", "success")
                                merged_pages.append(_r)

                        if next_idx < n_pdf_pages and terminated_reason is None:
                            terminated_reason = "too_many_page_failures"
                        if terminated_reason:
                            for pending_page in range(next_idx + 1, n_pdf_pages + 1):
                                merged_pages.append(
                                    {
                                        "page": pending_page,
                                        "status": "error",
                                        "error_code": "NOT_SCHEDULED",
                                        "error_detail": f"Skipped after termination: {terminated_reason}",
                                        "text": "",
                                        "lines_count": 0,
                                        "extracted_fields": {},
                                        "field_confidence": 0.0,
                                        "ai_enhanced": None,
                                    }
                                )

                        gather_page_errors = sum(
                            1 for _p in merged_pages if isinstance(_p, dict) and _p.get("status") == "error"
                        )
                        if gather_page_errors == 0:
                            ocr_job_outcome = "ok"
                        elif gather_page_errors >= len(merged_pages):
                            ocr_job_outcome = "failed"
                        else:
                            ocr_job_outcome = "partial"

                        logger.info(
                            "[ocr_metrics] scenario_d pages_seen=%s failed_pages=%s queue_wait_ms=%s page_process_ms=%s terminated_reason=%s",
                            next_idx,
                            gather_page_errors,
                            queue_wait_ms_total,
                            proc_ms_total,
                            terminated_reason,
                        )
                        if ocr_job_outcome != "ok":
                            logger.info(
                                "[ocr_metrics] partial_job outcome=%s failed_pages=%s/%s",
                                ocr_job_outcome,
                                gather_page_errors,
                                len(merged_pages),
                            )

                        logger.info("="*60)
                        logger.info(
                            "[SUCCESS] %s - Scenario D finished (%s, rows=%s)",
                            file.filename,
                            ocr_job_outcome,
                            len(merged_pages),
                        )
                        logger.info("="*60)
                        return {
                            "trace_id": trace_id,
                            "filename": file.filename,
                            "document_type": "multi_page_pdf",
                            "total_pages": len(image_paths),
                            "pages": merged_pages,
                            "ocr_job_outcome": ocr_job_outcome,
                            "terminated_reason": terminated_reason,
                            "provider": "multi_page_processor",
                            "processing_mode": processing_mode,
                            "processing_steps": {
                                "pdf_conversion": "completed",
                                "pages_processed": len(merged_pages),
                                "ai_enhancement": "completed" if _ai_processor.api_key else "skipped",
                            },
                        }

                else:
                    # ── Scenario A / B: single-page PDF ────────────────────────────────
                    # Classify the page first so invoices are never passed through OpenCV.
                    # OpenCV dominant-gap logic treats a large footer gap on an invoice as
                    # a two-document boundary, producing two incomplete records (the bug).
                    if processing_mode in ("AR", "AP"):
                        _raise_if_bg_job_cancelled(background_job_id)
                        # If the user already confirmed multiple receipts, skip the
                        # classifier entirely — treat the page as "receipts" unconditionally
                        # so that force-split can run without waiting for an AI round-trip.
                        if is_vlm_detection_backend():
                            single_page_class = "receipts"
                            logger.info(
                                "[ROUTER] Settings VLM Detect: single-page PDF → Detect "
                                "(invoice classifier skipped)",
                            )
                        elif processing_mode == "AP" and _ap_rs in ("single_per_page", "single_span_pages"):
                            single_page_class = "invoice"
                            logger.info(
                                "[ROUTER] %s single-page layout → invoice (AP user receipt signal %s)",
                                processing_mode,
                                _ap_rs,
                            )
                        elif multi_receipt_confirmed:
                            single_page_class = "receipts"
                            logger.info(
                                "[ROUTER] %s single-page layout → receipts (user-confirmed, classifier skipped)",
                                processing_mode,
                            )
                        else:
                            single_page_class = await _classify_document_layout(
                                image_paths[0], ocr_provider_name
                            )
                            logger.info(
                                "[ROUTER] %s single-page layout → %s",
                                processing_mode, single_page_class,
                            )
                        if (
                            not multi_receipt_confirmed
                            and not is_vlm_detection_backend()
                            and single_page_class == "receipts"
                            and CHEQUE_ROUTER_QUICK_PROBE_ENABLED
                        ):
                            cheque_router_probe = await _ar_ap_cheque_router_quick_probe(
                                image_paths[0], ocr_provider_name, ocr_model_override
                            )
                            if cheque_router_probe.get("matched"):
                                single_page_class = "invoice"
                                logger.info(
                                    "[ROUTER] %s single-page: cheque quick-probe → Scenario A, skip OpenCV",
                                    processing_mode,
                                )
                        if single_page_class == "receipts":
                            # Scenario B: composite receipt scan — Settings VLM Detect.
                            logger.info(
                                "[ROUTER] Scenario B: %s on single page.",
                                "Settings VLM Detect → crop → receipt_instance"
                                if is_vlm_detection_backend()
                                else "running OpenCV segmentation",
                            )
                            multi_receipt_result, ask_confirm = await _run_ap_multi_with_guess_autoconfirm(
                                image_paths[0],
                                trace_id=trace_id,
                                filename=file.filename or "",
                                ocr_provider_name=ocr_provider_name,
                                ocr_model_override=ocr_model_override,
                                ocr_prompt_override=ocr_prompt_override,
                                processing_mode=processing_mode,
                                multi_receipt_confirmed=multi_receipt_confirmed,
                                ap_receipt_signal=_ap_rs,
                                pdf_page_num=1,
                                background_job_id=background_job_id,
                                multi_receipt_kwargs=_multi_receipt_kwargs,
                            )
                            if multi_receipt_result is not None:
                                return multi_receipt_result
                            # Non-guess only: ask user to confirm force-split.
                            if ask_confirm:
                                return {
                                    "trace_id": trace_id,
                                    "filename": file.filename,
                                    "needs_confirmation": True,
                                    "message": "Multiple receipts suspected but could not be separated automatically. Please confirm to force-split.",
                                    "processing_mode": processing_mode,
                                }
                        else:
                            # Scenario A: structured invoice — skip segmentation entirely.
                            logger.info("[ROUTER] Scenario A: single invoice detected, skipping OpenCV segmentation.")
                    # Fall through to standard single-image processing (Scenario A path).
                    process_path = image_paths[0]
        else:
            # Regular image file (not PDF)
            process_path = tmp_path
            # ── Scenario A / B: classify before deciding whether to run OpenCV ───
            if processing_mode in ("AR", "AP"):
                _raise_if_bg_job_cancelled(background_job_id)
                # If the user already confirmed multiple receipts, skip the classifier
                # entirely — treat the image as "receipts" unconditionally so that
                # force-split can run without an extra AI round-trip.
                if is_vlm_detection_backend():
                    image_layout_class = "receipts"
                    logger.info(
                        "[ROUTER] Settings VLM Detect: image → Detect (invoice classifier skipped)",
                    )
                elif processing_mode == "AP" and _ap_rs in ("single_per_page", "single_span_pages"):
                    image_layout_class = "invoice"
                    logger.info(
                        "[ROUTER] %s image layout → invoice (AP user receipt signal %s)",
                        processing_mode,
                        _ap_rs,
                    )
                elif multi_receipt_confirmed:
                    image_layout_class = "receipts"
                    logger.info(
                        "[ROUTER] %s image layout → receipts (user-confirmed, classifier skipped)",
                        processing_mode,
                    )
                else:
                    image_layout_class = await _classify_document_layout(
                        tmp_path, ocr_provider_name
                    )
                    logger.info(
                        "[ROUTER] %s image layout → %s",
                        processing_mode, image_layout_class,
                    )
                if (
                    not multi_receipt_confirmed
                    and not is_vlm_detection_backend()
                    and image_layout_class == "receipts"
                    and CHEQUE_ROUTER_QUICK_PROBE_ENABLED
                ):
                    cheque_router_probe = await _ar_ap_cheque_router_quick_probe(
                        tmp_path, ocr_provider_name, ocr_model_override
                    )
                    if cheque_router_probe.get("matched"):
                        image_layout_class = "invoice"
                        logger.info(
                            "[ROUTER] %s image: cheque quick-probe → Scenario A, skip OpenCV",
                            processing_mode,
                        )
                if image_layout_class == "receipts":
                    # Scenario B: composite receipt scan — Settings VLM Detect.
                    logger.info(
                        "[ROUTER] Scenario B: %s on image.",
                        "Settings VLM Detect → crop → receipt_instance"
                        if is_vlm_detection_backend()
                        else "running OpenCV segmentation",
                    )
                    multi_receipt_result, ask_confirm = await _run_ap_multi_with_guess_autoconfirm(
                        tmp_path,
                        trace_id=trace_id,
                        filename=file.filename or "",
                        ocr_provider_name=ocr_provider_name,
                        ocr_model_override=ocr_model_override,
                        ocr_prompt_override=ocr_prompt_override,
                        processing_mode=processing_mode,
                        multi_receipt_confirmed=multi_receipt_confirmed,
                        ap_receipt_signal=_ap_rs,
                        pdf_page_num=1,
                        background_job_id=background_job_id,
                        multi_receipt_kwargs=_multi_receipt_kwargs,
                    )
                    if multi_receipt_result is not None:
                        return multi_receipt_result
                    # Non-guess only: ask user to confirm force-split.
                    if ask_confirm:
                        return {
                            "trace_id": trace_id,
                            "filename": file.filename,
                            "needs_confirmation": True,
                            "message": "Multiple receipts suspected but could not be separated automatically. Please confirm to force-split.",
                            "processing_mode": processing_mode,
                        }
                else:
                    # Scenario A: single invoice image — skip segmentation entirely.
                    logger.info("[ROUTER] Scenario A: single invoice image, skipping OpenCV segmentation.")
        
        # Step 1: Perform OCR (or use text extraction result)
        if is_pdf and process_path is None:
            logger.info("[STEP 2] Using PDF text extraction result.")
        else:
            logger.info("[STEP 2] Running OCR with %s...", ocr_provider_name)
            _raise_if_bg_job_cancelled(background_job_id)
            ocr_result = await _ocr_service.recognize(
                process_path,
                provider_name=ocr_provider_name,
                model=ocr_model_override,
                prompt_override=ocr_prompt_override,
            )
            logger.info(f"[OCR] Complete: Detected {len(ocr_result.lines)} lines, {len(ocr_result.text)} characters")
        
        # Step 2: Run field filtering (rule-based extraction)
        logger.info("[STEP 3] Extracting structured fields...")
        filtered_result = _filtering_pipeline.filter_and_extract(ocr_result)
        logger.info(f"[FIELDS] Extraction Complete: {len(filtered_result['fields'])} fields extracted")
        logger.info(f"   Confidence: {filtered_result['overall_confidence']:.1%}")
        if filtered_result['missing_fields']:
            logger.info(f"   Missing: {', '.join(filtered_result['missing_fields'])}")
        _record_processing_event(
            db,
            company_id=company_id,
            trace_id=trace_id,
            filename=file.filename or "",
            stage="ocr_complete",
            source="ocr_test_single",
            reason="ocr_and_filter_completed",
            outcome="completed",
            metadata={"mode": processing_mode, "document_type": "pdf" if is_pdf else "image"},
        )
        
        # Step 3a: Document Gate — classify before AI enhancement (AR/AP only)
        if processing_mode in ("AR", "AP") and not force_process:
            gate_result = classify_document(
                ocr_text=ocr_result.text,
                company_id=company_id,
                db=db,
            )
            logger.info("[Gate] Document classified as %s for mode=%s", gate_result, processing_mode)
            if gate_result == REFERENCE_FINANCIAL:
                subtype = infer_document_subtype(ocr_result.text)
                return {
                    "trace_id": trace_id,
                    "filename": file.filename,
                    "gate_result": REFERENCE_FINANCIAL,
                    "gate_document_subtype": subtype,
                    "gate_message": GATE_MESSAGES[REFERENCE_FINANCIAL],
                    "ocr_text": ocr_result.text,
                    "processing_mode": processing_mode,
                }
            elif gate_result == NON_FINANCIAL:
                return {
                    "trace_id": trace_id,
                    "filename": file.filename,
                    "gate_result": NON_FINANCIAL,
                    "gate_message": GATE_MESSAGES[NON_FINANCIAL],
                    "processing_mode": processing_mode,
                }
            elif gate_result == AMBIGUOUS:
                return {
                    "trace_id": trace_id,
                    "filename": file.filename,
                    "gate_result": AMBIGUOUS,
                    "gate_message": GATE_MESSAGES[AMBIGUOUS],
                    "ocr_text": ocr_result.text,
                    "processing_mode": processing_mode,
                }
            # TRANSACTIONAL — fall through to normal enhancement

        # Step 3b: field extraction / AI enhancement
        ai_enhanced_fields = None
        if processing_mode in ("AR", "AP"):
            # AR and AP: receipt/invoice or cheque branch via VLM (no DeepSeek).
            logger.info("[STEP 4] %s mode — extracting fields via structured OCR (no AI enhancement)...", processing_mode)
            _raise_if_bg_job_cancelled(background_job_id)
            ai_enhanced_fields = await _extract_ar_ap_ai_fields_routed(
                ocr_text=ocr_result.text,
                img_path=process_path,
                page_num=1,
                ocr_provider_name=ocr_provider_name,
                ocr_model_override=ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL,
                processing_mode=processing_mode,
                cheque_probe=cheque_router_probe,
                ocr_lines=ocr_result.lines,
                rescan_supplement=_rescan_supplement,
            )
            ai_enhanced_fields = await _ap_apply_cross_vlm_merge_if_configured(
                processing_mode=processing_mode,
                primary_model=ocr_model_override or AP_MULTI_RECEIPT_OCR_MODEL,
                ai_primary=ai_enhanced_fields,
                ocr_text=ocr_result.text,
                img_path=process_path,
                page_num=1,
                ocr_provider_name=ocr_provider_name,
                image_options=None,
                ocr_lines=ocr_result.lines,
                cheque_probe=cheque_router_probe,
                rescan_supplement=_rescan_supplement,
            )
            logger.info("[STEP 4] %s extraction complete.", processing_mode)
        elif _ai_processor.api_key:
            try:
                detected_type = _document_type_for_enhancement(processing_mode, ocr_result.text)
                logger.info(
                    f"[STEP 4] Running {settings.ai_enhance_model} enhancement (mode: {processing_mode}, document: {detected_type})..."
                )
                company_context = _load_company_context(db, company_id, detected_type)
                ai_enhanced = await _ai_processor.enhance_ocr_result(
                    ocr_result,
                    document_type=detected_type,
                    processing_mode=processing_mode,
                    metadata={
                        "company_context": company_context,
                    },
                )
                ai_enhanced_fields = ai_enhanced
                if isinstance(ai_enhanced_fields, dict):
                    _inject_trace_meta(ai_enhanced_fields, trace_id=trace_id)
                    context_meta = (
                        ai_enhanced_fields.get("context_meta")
                        if isinstance(ai_enhanced_fields.get("context_meta"), dict)
                        else {}
                    )
                    context_meta["rule_memory_mode"] = processing_mode
                    ai_enhanced_fields["context_meta"] = context_meta
                    # Stage 2: apply MD rule memory (3-tier priority, conflict flagging)
                    if isinstance(ai_enhanced_fields.get("transactions"), list):
                        _rule_md = _load_rule_memory_for_ocr(db, company_id, processing_mode)
                        ai_enhanced_fields["transactions"] = _apply_rules_from_memory(
                            ai_enhanced_fields["transactions"],
                            _rule_md,
                            ocr_result.text,
                        )
                        # Stage 2b: apply exclusion rules
                        _excl_rules = _load_exclusion_rules_for_ocr(db, company_id)
                        if _excl_rules:
                            ai_enhanced_fields["transactions"] = _apply_exclusions(
                                ai_enhanced_fields["transactions"],
                                _excl_rules,
                                ocr_result.text,
                                processing_mode,
                                db,
                            )
                _record_processing_event(
                    db,
                    company_id=company_id,
                    trace_id=trace_id,
                    filename=file.filename or "",
                    stage="ai_complete",
                    source="ocr_test_single",
                    reason="ai_post_process_success",
                    outcome="completed",
                    metadata={"mode": processing_mode, "document_type": detected_type},
                )
                logger.info("[AI] Enhancement Complete")
                logger.info(f"   AI Confidence: {ai_enhanced.get('confidence', 0):.1%}")
            except Exception as e:
                logger.warning(f"[WARN] AI enhancement failed (non-critical): {str(e)}")
                # Continue without AI enhancement if it fails
        else:
            logger.info("[STEP 4] Skipped (no AI enhancement API key configured)")
        
        logger.info("="*60)
        logger.info(f"[SUCCESS] {file.filename} processed successfully!")
        logger.info("="*60)
        
        return {
            "trace_id": trace_id,
            "filename": file.filename,
            "document_type": "single_page_pdf" if is_pdf else "image",
            "provider": ocr_result.metadata.get("source"),
            "text": ocr_result.text,
            "lines": [
                {
                    "text": line.text,
                    "confidence": line.confidence,
                    "bbox": line.bbox,
                }
                for line in ocr_result.lines
            ],
            "metadata": ocr_result.metadata,
            "extracted_fields": filtered_result["fields"],
            "field_confidence": filtered_result["overall_confidence"],
            "missing_fields": filtered_result["missing_fields"],
            "filter_status": filtered_result["status"],
            "ai_enhanced": ai_enhanced_fields,  # DeepSeek AI results
            "processing_mode": processing_mode,
            "processing_steps": {
                "pdf_text_extraction": "completed" if used_pdf_text_extraction else "skipped",
                "pdf_conversion": "skipped" if used_pdf_text_extraction else ("completed" if is_pdf else "not_applicable"),
                "1_ocr": "completed" if not used_pdf_text_extraction else "skipped",
                "2_field_filtering": "completed",
                "3_ai_enhancement": "completed" if ai_enhanced_fields else "skipped"
            }
        }
    except WorkflowRunCancelled:
        raise
    except HTTPException:
        raise
    except Exception as e:
        # Log full traceback for debugging
        logger.error(f"OCR processing failed: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to OCR: {str(e)}")
    finally:
        if _cv_reset is not None:
            _ap_cross_verify_force_cv.reset(_cv_reset)
        if _wf_reset is not None:
            _workflow_run_id_cv.reset(_wf_reset)
        try:
            await _ocr_cm.__aexit__(None, None, None)
        except Exception:
            pass

        # Clean up temporary files
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass  # Ignore cleanup errors
        
        # Clean up converted PDF images
        for img_path in image_paths:
            if os.path.exists(img_path):
                try:
                    os.remove(img_path)
                except Exception:
                    pass  # Ignore cleanup errors


@router.post("/ocr/ai-enhanced")
async def ocr_ai_enhanced(
    file: UploadFile = File(...),
    company_id: str = Depends(get_current_company_id),
    trace_id: str = Depends(get_trace_id),
    db: Session = Depends(get_db),
) -> dict:
    """
    OCR with AI post-processing
    Uses DeepSeek AI to correct errors and extract structured data
    """
    # Validate file
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")
    
    # Get file extension
    suffix = os.path.splitext(file.filename)[1].lower()
    if suffix not in [".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".webp"]:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file format: {suffix}"
        )
    
    # Read file content
    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        assert_upload_size(content)
        assert_file_type(file.filename or "upload.bin", content)
    except ValueError as exc:
        detail = str(exc)
        code = 413 if "maximum size" in detail.lower() else 400
        raise HTTPException(status_code=code, detail=detail) from exc

    # Save to temporary file
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, mode='wb') as tmp_file:
            tmp_file.write(content)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
            tmp_path = tmp_file.name
        
        # Verify file exists
        if not os.path.exists(tmp_path) or os.path.getsize(tmp_path) == 0:
            raise HTTPException(status_code=500, detail="Failed to save uploaded file")
        
        # Perform OCR (registry alias + default payload model from config)
        ocr_result = await _ocr_service.recognize(
            tmp_path,
            provider_name=settings.ocr_provider,
            model=settings.vlm_model,
        )
        _record_processing_event(
            db,
            company_id=company_id,
            trace_id=trace_id,
            filename=file.filename or "",
            stage="ocr_complete",
            source="ocr_ai_enhanced",
            reason="ocr_completed",
            outcome="completed",
            metadata={"mode": "ai_enhanced_endpoint"},
        )
        
        # AI post-processing
        detected_type = _detect_document_type(ocr_result.text)
        logger.info(f"[AI] Detected document type: {detected_type}")
        company_context = _load_company_context(db, company_id, detected_type)
        ai_enhanced_result = await _ai_processor.enhance_ocr_result(
            ocr_result,
            document_type=detected_type,
            metadata={
                "company_context": company_context,
                "multi_receipt_confirmed": multi_receipt_confirmed,
            },
        )
        _record_processing_event(
            db,
            company_id=company_id,
            trace_id=trace_id,
            filename=file.filename or "",
            stage="ai_complete",
            source="ocr_ai_enhanced",
            reason="ai_post_process_success",
            outcome="completed",
            metadata={"document_type": detected_type},
        )
        if isinstance(ai_enhanced_result, dict):
            _inject_trace_meta(ai_enhanced_result, trace_id=trace_id)
            context_meta = (
                ai_enhanced_result.get("context_meta")
                if isinstance(ai_enhanced_result.get("context_meta"), dict)
                else {}
            )
            context_meta["rule_memory_mode"] = processing_mode
            ai_enhanced_result["context_meta"] = context_meta
            # Stage 2: apply MD rule memory — deterministic pass after LLM (3-tier priority, conflict flagging)
            if isinstance(ai_enhanced_result.get("transactions"), list):
                _rule_md = _load_rule_memory_for_ocr(db, company_id, processing_mode or "AR")
                ai_enhanced_result["transactions"] = _apply_rules_from_memory(
                    ai_enhanced_result["transactions"],
                    _rule_md,
                    ocr_result.text,
                )
                # Stage 2b: apply exclusion rules
                _excl_rules = _load_exclusion_rules_for_ocr(db, company_id)
                if _excl_rules:
                    ai_enhanced_result["transactions"] = _apply_exclusions(
                        ai_enhanced_result["transactions"],
                        _excl_rules,
                        ocr_result.text,
                        processing_mode or "AR",
                        db,
                    )
        # Also run field filtering for comparison
        filtered_result = _filtering_pipeline.filter_and_extract(ocr_result)
        
        return {
            "trace_id": trace_id,
            "provider": ocr_result.metadata.get("source"),
            "ai_enhanced": ai_enhanced_result,
            "rule_based_extraction": {
                "extracted_fields": filtered_result["fields"],
                "field_confidence": filtered_result["overall_confidence"],
                "missing_fields": filtered_result["missing_fields"],
            },
            "raw_ocr": {
                "text": ocr_result.text,
                "lines": [
                    {
                        "text": line.text,
                        "confidence": line.confidence,
                        "bbox": line.bbox,
                    }
                    for line in ocr_result.lines
                ],
            },
            "metadata": {
                "filename": file.filename,
                "processing_method": f"{ocr_result.metadata.get('source', 'ocr')} + {settings.ai_enhance_model}",
                "timestamp": ocr_result.metadata.get("timestamp"),
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"AI-enhanced OCR failed: {str(e)}")
        logger.error(traceback.format_exc())
        raise HTTPException(status_code=500, detail=f"Failed to process: {str(e)}")
    finally:
        # Clean up temporary file
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass

"""BANK PDF probe + document dispatcher (Slice 1).

All BANK+PDF entries must probe then route to a bank adapter.
Identified HSBC → HSBC adapter path inside BankStatementParser.
BANK mode must never fall through to OCR Scenario D.

No page-index / row-ordinal / amount / payee hardcodes.
No real customer statement content.
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable, Literal

logger = logging.getLogger(__name__)

RouteName = Literal[
    "hsbc_adapter",
    "bank_adapter",
    "generic_bank",
    "unconfigured",
]

# Layout signals only — bank keywords come from bank_prompts registry.
_TXN_HEADER_PATTERNS = (
    re.compile(r"\bdeposit\b", re.I),
    re.compile(r"\bwithdrawal\b", re.I),
    re.compile(r"\bbalance\b", re.I),
    re.compile(r"transaction\s+details", re.I),
)


@dataclass
class BankProbe:
    bank_id: str
    text_rich: bool
    has_txn_header: bool
    char_count: int
    route: RouteName
    adapter: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _detect_bank_id(text: str) -> str:
    """Match bank keywords from the shared registry (no page-number logic)."""
    from app.bank_prompts import BANK_KEYWORDS

    upper = (text or "").upper()
    if not upper.strip():
        return "UNKNOWN"
    for bank, patterns in BANK_KEYWORDS.items():
        for pattern in patterns:
            if pattern and pattern.upper() in upper:
                return bank
    return "UNKNOWN"


def _has_txn_header(text: str) -> bool:
    t = text or ""
    # Require deposit + withdrawal + balance style header signals
    hits = sum(1 for p in _TXN_HEADER_PATTERNS if p.search(t))
    return hits >= 2


def inspect_bank_pdf(file_path: str, *, min_chars: int = 200) -> BankProbe:
    """Probe a PDF using limited text extraction (first pages aggregate)."""
    char_count = 0
    sample = ""
    try:
        import fitz

        doc = fitz.open(file_path)
        try:
            parts: list[str] = []
            # Read up to first 3 pages for bank id / header — not a page-index rule
            for i in range(min(3, len(doc))):
                parts.append(doc[i].get_text() or "")
            sample = "\n".join(parts)
            # Full-doc char count for text-rich (still not used as page branching)
            for page in doc:
                char_count += len(page.get_text() or "")
        finally:
            doc.close()
    except Exception as exc:
        logger.warning("[BANK-DISPATCH] probe text extraction failed: %s", exc)
        sample = ""
        char_count = 0

    bank_id = _detect_bank_id(sample)
    text_rich = char_count >= int(min_chars)
    has_hdr = _has_txn_header(sample)

    if bank_id == "HSBC":
        route: RouteName = "hsbc_adapter"
        adapter = "hsbc_adapter_v2"
    elif bank_id != "UNKNOWN":
        route = "bank_adapter"
        adapter = f"{bank_id.lower()}_adapter"
    else:
        route = "generic_bank"
        adapter = "generic_bank_adapter"

    probe = BankProbe(
        bank_id=bank_id,
        text_rich=text_rich,
        has_txn_header=has_hdr,
        char_count=char_count,
        route=route,
        adapter=adapter,
    )
    logger.info(
        "[BANK-DISPATCH] bank_id=%s route=%s adapter=%s text_rich=%s has_txn_header=%s chars=%d",
        probe.bank_id,
        probe.route,
        probe.adapter,
        probe.text_rich,
        probe.has_txn_header,
        probe.char_count,
    )
    return probe


def route_forbids_scenario_d(probe: BankProbe) -> bool:
    """BANK statement routes must never use OCR Scenario D."""
    return probe.route in {"hsbc_adapter", "bank_adapter", "generic_bank"}


async def dispatch_bank_pdf(
    file_path: str,
    *,
    file_type: str = "pdf",
    company_identity: dict[str, Any] | None = None,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Probe then parse via BankStatementParser (HSBC → dedicated adapter path)."""
    probe = inspect_bank_pdf(file_path)
    if not route_forbids_scenario_d(probe):
        raise RuntimeError("BANK dispatcher produced a route that allows Scenario D")

    from app.services.bank_statement_parser import BankStatementParser

    parser = BankStatementParser()
    result = await parser.parse_statement(
        file_path,
        file_type,
        company_identity=company_identity,
        progress_callback=progress_callback,
    )
    if not isinstance(result, dict):
        result = {"transactions": result or [], "bank": probe.bank_id, "count": 0}
    result["bank_probe"] = probe.to_dict()
    result["dispatcher_route"] = probe.route
    result["dispatcher_adapter"] = probe.adapter
    # Harden: if probe said HSBC, surface mismatch when parser disagrees
    parsed_bank = str(result.get("bank") or "").upper()
    if probe.bank_id == "HSBC" and parsed_bank not in {"HSBC", "UNKNOWN", ""}:
        logger.warning(
            "[BANK-DISPATCH] probe=HSBC but parser bank=%s — keeping probe route metadata",
            parsed_bank,
        )
    return result


def bank_ocr_response_from_parser_result(
    *,
    filename: str,
    trace_id: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    """Shape BankStatementParser output for ocr_test_core callers."""
    txns = list(result.get("transactions") or [])
    probe = result.get("bank_probe") or {}
    return {
        "trace_id": trace_id,
        "filename": filename,
        "document_type": "bank_statement",
        "processing_mode": "BANK",
        "provider": "bank_document_dispatcher",
        "bank": result.get("bank"),
        "transactions": txns,
        "count": result.get("count", len(txns)),
        "pages_processed": result.get("pages_processed"),
        "page_verification": result.get("page_verification"),
        "bank_probe": probe,
        "dispatcher_route": result.get("dispatcher_route"),
        "dispatcher_adapter": result.get("dispatcher_adapter"),
        "scenario_d_used": False,
        "ocr_preview_text": result.get("ocr_preview_text", ""),
        "processing_steps": {
            "bank_dispatch": "completed",
            "scenario_d": "skipped",
            "adapter": result.get("dispatcher_adapter"),
        },
    }

"""
Multi-Bank Statement Parser for Hong Kong Banks
Supports: BOC, HSBC, Hang Seng, Standard Chartered, etc.
"""
import asyncio
import hashlib
import logging
import os
import re
import tempfile
import time
from datetime import datetime
from typing import Any, Callable, Dict, List

import pandas as pd

from app.services.extraction_validation import finalize_bank_transactions

logger = logging.getLogger(__name__)


def _bank_vlm_r2_max_tokens() -> int:
    """Round-2 dual-track retry output token budget.

    Default 8192: many OpenAI-compatible gateways return 400 for very large
    ``max_tokens`` (e.g. 24576), which breaks fallback models on the same host.
    Set BANK_VLM_R2_MAX_TOKENS to raise the cap when your provider supports it.
    """
    raw = os.getenv("BANK_VLM_R2_MAX_TOKENS", "8192").strip()
    try:
        v = int(raw)
    except ValueError:
        v = 8192
    return max(1024, min(v, 65536))


class BankStatementParser:
    _OCR_CACHE_TTL_SECONDS = 60 * 60
    _OCR_CACHE_MAX_ENTRIES = 256
    _ocr_page_cache: dict[str, tuple[float, str]] = {}

    # Lightweight prompt used only to identify the bank from a single page image.
    # max_tokens=64 is sufficient — the response is a tiny JSON object.
    _BANK_IDENTIFICATION_PROMPT: str = (
        "Identify which bank issued this statement image.\n"
        "Return ONLY a JSON object: {\"bank_id\": \"CODE\"}\n\n"
        "Codes:\n"
        "  SCB       — Standard Chartered Bank / 渣打銀行\n"
        "  BOC       — Bank of China Hong Kong / 中國銀行(香港)\n"
        "  BOCOM     — Bank of Communications / 交通銀行\n"
        "  OCBC      — OCBC Bank\n"
        "  HSBC      — HSBC / 滙豐銀行\n"
        "  HANG_SENG — Hang Seng Bank / 恆生銀行\n"
        "  DBS       — DBS Bank / 星展銀行\n"
        "  BEA       — Bank of East Asia / BEA HK\n"
        "  UNKNOWN   — cannot determine\n\n"
        "Output ONLY the JSON object, nothing else."
    )

    @staticmethod
    def _emit_progress(
        progress_callback: Callable[[dict[str, Any]], None] | None,
        **payload: Any,
    ) -> None:
        if not progress_callback:
            return
        try:
            progress_callback(payload)
        except Exception:
            logger.debug("Progress callback failed", exc_info=True)

    def __init__(self):
        from app.bank_prompts import BANK_KEYWORDS as _pkg_keywords
        # Banks with specific VLM prompts come from the package (BOC, OCBC, …).
        # Banks without specific prompts are kept here for text-based PDF routing only.
        self.bank_patterns = {
            **_pkg_keywords,
            # HSBC / Hang Seng / SCB keywords come from bank_prompts via _pkg_keywords.
            'DBS':       ['星展', 'DBS Bank'],
        }

    @classmethod
    def _get_cached_ocr_text(cls, cache_key: str) -> str | None:
        entry = cls._ocr_page_cache.get(cache_key)
        if not entry:
            return None
        ts, text = entry
        if (time.time() - ts) > cls._OCR_CACHE_TTL_SECONDS:
            cls._ocr_page_cache.pop(cache_key, None)
            return None
        return text

    @classmethod
    def _set_cached_ocr_text(cls, cache_key: str, text: str) -> None:
        now = time.time()
        cls._ocr_page_cache[cache_key] = (now, text)
        if len(cls._ocr_page_cache) <= cls._OCR_CACHE_MAX_ENTRIES:
            return

        # Evict expired entries first, then oldest entries.
        expired_keys = [
            key for key, (ts, _) in cls._ocr_page_cache.items()
            if (now - ts) > cls._OCR_CACHE_TTL_SECONDS
        ]
        for key in expired_keys:
            cls._ocr_page_cache.pop(key, None)

        if len(cls._ocr_page_cache) <= cls._OCR_CACHE_MAX_ENTRIES:
            return

        ordered = sorted(cls._ocr_page_cache.items(), key=lambda item: item[1][0])
        overflow = len(cls._ocr_page_cache) - cls._OCR_CACHE_MAX_ENTRIES
        for idx in range(max(overflow, 0)):
            cls._ocr_page_cache.pop(ordered[idx][0], None)
    
    async def parse_statement(
        self,
        file_path: str,
        file_type: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        """Parse bank statement and detect bank"""
        logger.info(f"Parsing bank statement: {file_path} (type: {file_type})")
        
        if file_type == 'csv':
            return await self.parse_csv(file_path)
        elif file_type in ['xlsx', 'xls']:
            return await self.parse_excel(file_path)
        elif file_type == 'pdf':
            return await self.parse_pdf_statement(
                file_path,
                company_identity=company_identity,
                progress_callback=progress_callback,
            )
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
    
    async def parse_pdf_statement(
        self,
        file_path: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> Dict[str, Any]:
        """Parse PDF bank statement with table extraction and OCR fallback"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise ImportError("PyMuPDF (fitz) is required for PDF parsing. Install with: pip install pymupdf")
        
        doc = fitz.open(file_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        self._emit_progress(
            progress_callback,
            percent=10,
            label="已完成 PDF 文字抽取",
            page_current=0,
            page_total=len(doc),
        )
        
        logger.info(f"Extracted text length: {len(full_text)} characters")
        logger.debug(f"First 500 chars: {full_text[:500]}")
        
        # Detect bank from text
        bank_name = self._detect_bank(full_text)
        logger.info(f"Detected bank from text extraction: {bank_name}")
        self._emit_progress(
            progress_callback,
            percent=15,
            label="銀行識別中",
            page_current=0,
            page_total=len(doc),
        )
        
        pages_processed = len(doc)
        page_verification_out: Dict[int, str] = {}

        # Image-based PDF fast path: minimal extractable text means PyMuPDF table parsers
        # will find nothing. Skip the old qwen-vl-ocr-latest detection step and go directly
        # to the single-stage VLM pipeline (qwen3.5-plus-2026-02-15).
        if len(full_text.strip()) < 100:
            logger.info(
                "[BANK] Minimal text extracted — image-based PDF detected. "
                "Routing directly to VLM single-stage pipeline (skipping table parsers)."
            )
            # Text detection is impossible for image-based PDFs.  Run a lightweight
            # VLM bank-identification pre-pass on page 1 so the full pipeline can
            # use the correct bank-specific dual-track prompt (e.g. SCB) instead of
            # falling back to DEFAULT-only mode and timing out.
            if bank_name == 'UNKNOWN':
                bank_name = await self._identify_bank_from_image(file_path)
                logger.info(f"[BANK] Image-based PDF bank identification: {bank_name}")
            if bank_name == 'SCB':
                # Route ALL SC PDFs through _parse_scb_statement (PyMuPDF → VLM fallback)
                # so the entry point is consistent regardless of PDF type.
                # For image-based PDFs PyMuPDF finds nothing in ~100ms then falls to VLM.
                transactions = await self._parse_scb_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'BEA':
                transactions = await self._parse_bea_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'BOC':
                # Route BOC through PyMuPDF table parser first (same pattern as SCB/HSBC).
                # Image-based PDFs get no tables in ~100ms, then _parse_boc_statement
                # falls back to VLM inside — so VLM handles image-based as backup.
                transactions = await self._parse_boc_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'HSBC':
                transactions = await self._parse_hsbc_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'HANG_SENG':
                transactions = await self._parse_hang_seng_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'BOCOM':
                transactions = await self._parse_bocom_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            else:
                transactions = await self._parse_with_ocr_fallback(
                    file_path,
                    bank_name,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            # When PyMuPDF fails (0 transactions), bank parsers call VLM internally; also
            # run backup here so we never leave image-based path without trying VLM.
            if not transactions:
                logger.info(
                    "[BANK] Image-based path returned no transactions — calling VLM backup."
                )
                transactions = await self._parse_with_ocr_fallback(
                    file_path,
                    bank_name,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
        else:
            # Text-based PDF: use bank-specific PyMuPDF table parser.
            # Banks that only have a VLM prompt (no table parser) skip straight to VLM.
            from app.bank_prompts import BANK_PROMPT_DATABASE
            _vlm_only_banks = {
                b for b in BANK_PROMPT_DATABASE
                if b not in ('DEFAULT', 'BEA', 'BOC', 'HSBC', 'HANG_SENG', 'SCB', 'BOCOM')
            }

            if bank_name == 'BOC':
                transactions = await self._parse_boc_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'HSBC':
                transactions = await self._parse_hsbc_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'BEA':
                transactions = await self._parse_bea_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'HANG_SENG':
                transactions = await self._parse_hang_seng_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'SCB':
                transactions = await self._parse_scb_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name == 'BOCOM':
                transactions = await self._parse_bocom_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            elif bank_name in _vlm_only_banks:
                # Banks with a specific VLM prompt but no table parser (e.g. OCBC)
                # → go straight to dual-track VLM, skip the BOC table fallback.
                logger.info(
                    f"[BANK] {bank_name} is VLM-only — routing directly to dual-track VLM pipeline."
                )
                transactions = await self._parse_with_ocr_fallback(
                    file_path,
                    bank_name,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )
            else:
                # UNKNOWN bank — try BOC table parser as structural fallback,
                # but tell its internal VLM fallback to use UNKNOWN (DEFAULT prompt only)
                # so a bank-specific wrong prompt is never applied.
                logger.info("Bank unknown, trying BOC parser first as fallback")
                transactions = await self._parse_boc_statement(
                    file_path,
                    full_text,
                    company_identity=company_identity,
                    bank_type_for_fallback=bank_name,
                    page_verification_out=page_verification_out,
                )

            # If table parser found nothing despite having text, fall back to VLM
            if not transactions:
                logger.info(
                    f"[BANK] {bank_name} table parser found no transactions — "
                    "falling back to VLM single-stage pipeline."
                )
                transactions = await self._parse_with_ocr_fallback(
                    file_path,
                    bank_name,
                    company_identity=company_identity,
                    progress_callback=progress_callback,
                    page_verification_out=page_verification_out,
                )

        logger.info(f"Parsed {len(transactions)} transactions from {bank_name} statement")
        
        # Calculate transactions per page breakdown
        transactions_per_page = {}
        if transactions:
            # Try to group by page (if page info available in metadata)
            # For now, estimate evenly distributed
            avg_per_page = len(transactions) // pages_processed if pages_processed > 0 else 0
            for i in range(pages_processed):
                transactions_per_page[i + 1] = avg_per_page
        
        out: Dict[str, Any] = {
            'bank': bank_name,
            'transactions': transactions,
            'count': len(transactions),
            'pages_processed': pages_processed,
            'transactions_per_page': transactions_per_page,
            'avg_transactions_per_page': len(transactions) / pages_processed if pages_processed > 0 else 0,
            # Keep a lightweight OCR/text preview for BANK chat without triggering a second /ocr/test call.
            'ocr_preview_text': (full_text or '')[:12000],
            'ocr_preview_source': 'ocr_or_pdf_text',
        }
        if page_verification_out:
            out["page_verification"] = {str(k): v for k, v in sorted(page_verification_out.items())}
        return out
    
    def _detect_bank(self, text: str) -> str:
        """Detect bank from statement text (case-insensitive)."""
        text_upper = text.upper()
        for bank, patterns in self.bank_patterns.items():
            for pattern in patterns:
                if pattern.upper() in text_upper:
                    logger.info(f"Matched bank pattern: {pattern!r} -> {bank}")
                    return bank
        logger.warning("No bank pattern matched, returning UNKNOWN")
        return 'UNKNOWN'
    
    def _merge_multiline_rows(self, rows: List[List]) -> List[List]:
        """Merge continuation rows that don't start with dates"""
        merged = []
        current_row = None
        
        for row in rows:
            first_cell = str(row[0]).strip() if row and row[0] else ''
            
            # If this row starts with a date or special marker, it's a new transaction
            if self._is_date_field(first_cell) or '承前結餘' in first_cell or '今期結餘' in first_cell:
                if current_row:
                    merged.append(current_row)
                current_row = list(row)
            else:
                # This is a continuation row - merge with current
                if current_row and len(row) > 2:
                    # Merge description (column 2)
                    current_row[2] = str(current_row[2]) + '\n' + str(row[2]) if current_row[2] else str(row[2])
                elif not current_row:
                    # First row is continuation? Skip it
                    continue
        
        if current_row:
            merged.append(current_row)
        
        return merged
    
    async def _parse_boc_statement(
        self,
        file_path: str,
        full_text: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        bank_type_for_fallback: str = 'BOC',
        page_verification_out: Dict[int, str] | None = None,
    ) -> List[Dict]:
        """Parse Bank of China specific format"""
        import fitz
        doc = fitz.open(file_path)
        transactions = []
        
        logger.info(f"Starting BOC statement parsing: {file_path}")
        logger.info(f"Document has {len(doc)} pages")
        
        for page_num, page in enumerate(doc):
            logger.info(f"Processing page {page_num + 1}/{len(doc)}")
            self._emit_progress(
                progress_callback,
                percent=min(80, 20 + int(((page_num + 1) / max(len(doc), 1)) * 60)),
                label=f"表格解析中（第 {page_num + 1}/{len(doc)} 頁）",
                page_current=page_num + 1,
                page_total=len(doc),
            )
            page_text_hint = page.get_text() or ""
            inferred_account_type = self._infer_account_type_from_text(page_text_hint)
            
            # Try multiple table extraction strategies
            tables_list = []
            
            # Strategy 1: Strict line detection (for tables with clear borders)
            try:
                tables = page.find_tables(
                    vertical_strategy="lines_strict",
                    horizontal_strategy="lines_strict",
                    snap_tolerance=5
                )
                tables_list = list(tables) if tables else []
                logger.info(f"Page {page_num + 1}: Strict strategy found {len(tables_list)} table(s)")
            except Exception as e:
                logger.warning(f"Strict table detection failed: {e}")
            
            # Strategy 2: Try "lines" (less strict) if strict failed
            if not tables_list:
                try:
                    tables = page.find_tables(
                        vertical_strategy="lines",
                        horizontal_strategy="lines",
                        snap_tolerance=10
                    )
                    tables_list = list(tables) if tables else []
                    logger.info(f"Page {page_num + 1}: Lines strategy found {len(tables_list)} table(s)")
                except Exception as e:
                    logger.warning(f"Lines table detection failed: {e}")
            
            # Strategy 3: Try "text" (implicit tables) if lines failed
            if not tables_list:
                try:
                    tables = page.find_tables(
                        vertical_strategy="text",
                        horizontal_strategy="text",
                        snap_tolerance=15
                    )
                    tables_list = list(tables) if tables else []
                    logger.info(f"Page {page_num + 1}: Text strategy found {len(tables_list)} table(s)")
                except Exception as e:
                    logger.warning(f"Text table detection failed: {e}")
            
            if not tables_list:
                logger.warning(f"No tables found on page {page_num + 1} with any strategy")
                continue
            
            for table_idx, table in enumerate(tables_list):
                logger.info(f"Processing table {table_idx + 1} on page {page_num + 1}")
                rows = table.extract()
                logger.info(f"Table {table_idx + 1}: {len(rows)} rows before merging")
                
                # Log first few rows for debugging
                for idx, row in enumerate(rows[:5]):
                    logger.debug(f"Row {idx}: {row}")
                
                # Merge multi-line rows
                merged_rows = self._merge_multiline_rows(rows)
                logger.info(f"Table {table_idx + 1}: {len(merged_rows)} rows after merging")
                
                # BOC format typically: 交易日期 | 起息/生效日期 | 交易摘要 | 存入 | 提取 | 原幣結餘
                # Or English: Date | Value Date | Description | Deposit | Withdrawal | Balance
                for row_idx, row in enumerate(merged_rows):
                    if not row or len(row) < 3:
                        continue
                    
                    # Skip header rows and special rows
                    first_cell = str(row[0]).strip() if row[0] else ''
                    if any(keyword in first_cell for keyword in ['交易日期', 'Date', '承前結餘', '今期結餘', '存入', '提取']):
                        logger.debug(f"Skipping special row: {first_cell}")
                        continue
                    
                    if not first_cell:
                        continue
                    
                    # Check if first column contains a date (YYYY/MM/DD or DD/MM/YYYY)
                    if not self._is_date_field(first_cell):
                        logger.debug(f"Skipping non-date row: {first_cell}")
                        continue
                    
                    try:
                        # Extract transaction data
                        date_str = first_cell
                        description = str(row[2]).strip() if len(row) > 2 and row[2] else ''
                        deposit_str = str(row[3]).strip() if len(row) > 3 and row[3] else '0'
                        withdrawal_str = str(row[4]).strip() if len(row) > 4 and row[4] else '0'
                        balance_str = str(row[5]).strip() if len(row) > 5 and row[5] else ''
                        
                        # Parse amounts (remove commas, handle empty)
                        deposit = self._parse_amount(deposit_str)
                        withdrawal = self._parse_amount(withdrawal_str)
                        balance = self._parse_amount(balance_str) if balance_str else None
                        
                        # Calculate amount (positive for deposit, negative for withdrawal)
                        amount = deposit if deposit > 0 else -withdrawal
                        
                        # Extract reference (e.g., cheque number from description)
                        reference = self._extract_reference(description)
                        
                        transaction = {
                            'date': self._normalize_date(date_str),
                            'bank_date': self._normalize_date(date_str),
                            'description': description,
                            'description_raw': description,
                            'deposit': deposit,
                            'withdrawal': withdrawal,
                            'amount': amount,
                            'balance': balance,
                            'reference': reference,
                            'currency': 'HKD',
                            'transaction_type': self._classify_transaction(description),
                            'account_type': inferred_account_type,
                            '賬戶類型': inferred_account_type,
                        }
                        
                        transactions.append(transaction)
                        logger.debug(f"Parsed transaction: {date_str} - {description} - {amount}")
                        
                    except Exception as e:
                        logger.error(f"Failed to parse row {row_idx}: {row}")
                        logger.error(f"Error: {e}", exc_info=True)
                        continue
        
        # Log summary
        logger.info(f"Successfully parsed {len(transactions)} transactions from BOC statement")
        if len(transactions) < 5:
            logger.warning(f"Only {len(transactions)} transactions found - this might be incomplete")
        
        # If no transactions found, try OCR fallback
        if not transactions:
            logger.warning("No transactions extracted from tables, trying OCR fallback")
            transactions = await self._parse_with_ocr_fallback(
                file_path,
                bank_type_for_fallback,
                company_identity=company_identity,
                progress_callback=progress_callback,
                page_verification_out=page_verification_out,
            )
        
        return transactions
    
    # ──────────────────────────────────────────────────────────────────────────
    # HSBC-SPECIFIC PIPELINE (pre-scan + OpenCV line annotation + retry loop)
    # All methods below are HSBC-only and are NOT called by any other bank.
    # ──────────────────────────────────────────────────────────────────────────

    # Month tokens for "12 Jul" row labels (V2 merge) — same as prescan date_labels.
    _HSBC_LABEL_MONTH_MAP: dict[str, int] = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    @staticmethod
    def _hsbc_partial_label_to_iso(
        label: str, header_year: int, header_month: int
    ) -> str:
        """Map a row date label like '12 Jul' to YYYY-MM-DD using HSBC header (Y, M).

        Same rule as bank_prompts/hsbc.py: if transaction month m > header month M,
        the row is in the prior calendar year (Y - 1); otherwise year Y.
        """
        import datetime as _dt

        if not label or not header_year or not header_month:
            return ""
        parts = label.strip().split()
        if len(parts) < 2:
            return ""
        try:
            day = int(parts[0])
            mon = BankStatementParser._HSBC_LABEL_MONTH_MAP.get(
                parts[1].lower().rstrip("."), 0
            )
            if not mon:
                return ""
            m, M, Y = mon, header_month, header_year
            year = (Y - 1) if m > M else Y
            return _dt.date(year, m, day).isoformat()
        except (ValueError, OverflowError):
            return ""

    @staticmethod
    def _hsbc_header_year_month(page) -> tuple[int, int] | None:
        """Read statement issue date (day + month + year) from the top ~30% of the page.

        HSBC prints e.g. '21 July 2022' in the header band. We scan that band via PyMuPDF
        text (not the table rows). If several dates appear, we take the **last** match in
        reading order — typically the statement date sits right of other header text.
        """
        import fitz as _fitz
        import re as _re

        _MON = (
            r"January|February|March|April|May|June|July|August|September|October|November|December|"
            r"Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
        )
        _pat = _re.compile(
            rf"\b(?P<d>[0-3]?\d)\s+(?P<mon>{_MON})\.?\s+(?P<y>(?:19|20)\d{{2}})\b",
            _re.IGNORECASE,
        )
        _mon_to_num = {
            "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
            "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
            "december": 12,
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }

        h = float(page.rect.height)
        clip = _fitz.Rect(0, 0, float(page.rect.width), h * 0.30)
        text = page.get_text("text", clip=clip) or ""
        matches = list(_pat.finditer(text))
        if not matches:
            return None
        m = matches[-1]
        try:
            y = int(m.group("y"))
            if y < 1990 or y > 2099:
                return None
            mon_s = m.group("mon").lower().rstrip(".")
            month = _mon_to_num.get(mon_s, 0)
            if not month:
                return None
            return y, month
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _hsbc_label_to_date_sliding_window(label: str) -> str:
        """Convert '7 Nov' → YYYY-MM-DD using host-year ±1 (legacy V2 fallback)."""
        import datetime as _dt

        if not label:
            return ""
        parts = label.strip().split()
        if len(parts) < 2:
            return ""
        try:
            day = int(parts[0])
            mon = BankStatementParser._HSBC_LABEL_MONTH_MAP.get(
                parts[1].lower().rstrip("."), 0
            )
            if not mon:
                return ""
            today = _dt.date.today()
            for yr in (today.year, today.year - 1, today.year + 1):
                try:
                    return _dt.date(yr, mon, day).isoformat()
                except ValueError:
                    continue
        except (ValueError, IndexError):
            pass
        return ""

    @staticmethod
    def _hsbc_prescan_count(page) -> tuple[int, int, int]:
        """Count transaction amounts in the Deposit/Withdrawal columns via
        PyMuPDF word-position data (text-based PDFs only).

        Returns (deposit_count, withdrawal_count, total).
        Returns (0, 0, 0) when no column headers are found (cover/portfolio page).
        """
        import re as _re

        # Matches monetary amounts: "1,234.56", "500.00", "12345.67", "0.22"
        AMOUNT_RE = _re.compile(
            r'^\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$'
            r'|^\d{4,}(?:\.\d{1,2})?$'
            r'|^\d+\.\d{2}$'
        )

        words = page.get_text("words")   # (x0, y0, x1, y1, text, blk, ln, wi)
        if not words:
            return 0, 0, 0

        page_width = page.rect.width

        # ── Locate column header x-positions ─────────────────────────────────
        dep_hdr_x: float | None = None
        wdw_hdr_x: float | None = None
        header_y:  float | None = None
        for w in words:
            txt = w[4].strip().lower()
            if txt == "deposit" and dep_hdr_x is None:
                dep_hdr_x = (w[0] + w[2]) / 2
                header_y  = w[1]
            elif txt == "withdrawal" and wdw_hdr_x is None:
                wdw_hdr_x = (w[0] + w[2]) / 2

        if dep_hdr_x is None and wdw_hdr_x is None:
            # No column header → cover page or portfolio summary → skip
            return 0, 0, 0

        if dep_hdr_x is None:
            dep_hdr_x = page_width * 0.64   # fallback: typical HSBC A4 ratio
        if wdw_hdr_x is None:
            wdw_hdr_x = page_width * 0.76

        # ±30 pt window around each column midpoint (tighter than ±40 to avoid
        # balance-column bleed — the Balance column is only ~70pt right of Withdrawal)
        dep_lo, dep_hi = dep_hdr_x - 30.0, dep_hdr_x + 30.0
        wdw_lo, wdw_hi = wdw_hdr_x - 30.0, wdw_hdr_x + 30.0
        min_y = (header_y or 0) + 4   # skip the header row itself

        deposit_count    = 0
        withdrawal_count = 0

        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
            if y0 < min_y:
                continue
            if not AMOUNT_RE.match(text):
                continue
            # Skip Date column (day numbers, year numbers like "2025")
            x_mid = (x0 + x1) / 2
            if x_mid < page_width * 0.22:
                continue
            amt_val_v1 = float(text.replace(",", ""))
            # Skip year-like numbers (e.g. "2025", "2026") — never transaction amounts
            if text.isdigit() and 1900 <= amt_val_v1 <= 2100:
                continue
            # Skip day numbers 1–31 appearing as bare integers (no decimal).
            # In the FCY section, HSBC prints full dates like "1 Feb" / "31 Jan"
            # whose day portion can land inside the Deposit/Withdrawal x-window.
            # Real amounts always appear with decimal places ("1.00", "31.00") in PDFs.
            if text.isdigit() and 1 <= amt_val_v1 <= 31:
                continue
            if dep_lo <= x_mid <= dep_hi:
                deposit_count += 1
            elif wdw_lo <= x_mid <= wdw_hi:
                withdrawal_count += 1

        total = deposit_count + withdrawal_count
        logger.debug(
            "[HSBC-PRESCAN] dep=%d, wdw=%d, total=%d "
            "(dep_col_x=%.1f, wdw_col_x=%.1f)",
            deposit_count, withdrawal_count, total, dep_hdr_x, wdw_hdr_x,
        )
        return deposit_count, withdrawal_count, total

    @staticmethod
    def _hsbc_prescan_amounts(page) -> dict:
        """Extract ALL amounts, section headers, and date labels from page text (V2 pipeline).

        Returns a dict with:
          "amounts"  : list of {"y", "col" ("Cr"|"Dr"), "amount", "text"}
                       sorted by y ascending — these are the ground-truth Dr/Cr values
          "balances" : list of {"y", "amount"} — Balance column values (day-end only)
          "sections" : list of {"y", "header"} — account section header positions
          "date_labels": list of {"y", "label"} — e.g. {"y": 102.3, "label": "7 Nov"}
          "header_y" : y-position of the column header row (or 0 if not found)
          "dep_hdr_x": x-centre of Deposit column
          "wdw_hdr_x": x-centre of Withdrawal column
          "bal_hdr_x": x-centre of Balance column
          "page_height": page height in PDF pts
          "no_table"  : True when no column headers found (cover/portfolio page)
        """
        import re as _re

        AMOUNT_RE = _re.compile(
            r'^\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$'
            r'|^\d{4,}(?:\.\d{1,2})?$'
            r'|^\d+\.\d{2}$'
        )

        # Month abbreviations for date-label detection
        MONTHS = {"jan", "feb", "mar", "apr", "may", "jun",
                  "jul", "aug", "sep", "oct", "nov", "dec"}

        SECTION_STRINGS = [
            "HSBC Business Direct Foreign Currency Savings",
            "HSBC Business Direct HKD Savings",
            "HSBC Business Direct HKD Current",
        ]

        words = page.get_text("words")   # (x0, y0, x1, y1, text, blk, ln, wi)
        page_width  = page.rect.width
        page_height = page.rect.height

        empty_result = {
            "amounts": [], "balances": [], "sections": [], "date_labels": [],
            "header_y": 0.0, "dep_hdr_x": page_width * 0.64,
            "wdw_hdr_x": page_width * 0.76, "bal_hdr_x": page_width * 0.88,
            "page_height": page_height,
            "page_width": page_width,
            "no_table": True,
            "_words": words or [],
        }

        if not words:
            return empty_result

        # Debug: log first ~30 words so backend log reveals what PyMuPDF sees
        _sample = [w[4].strip() for w in words[:30]]
        logger.debug("[HSBC-PRESCAN-V2] First 30 words: %s", _sample)

        # ── Locate column header x-positions ────────────────────────────────
        dep_hdr_x: float | None = None
        wdw_hdr_x: float | None = None
        bal_hdr_x: float | None = None
        header_y:  float | None = None

        _DEPOSIT_WORDS = {"deposit", "deposits", "credit", "cr"}
        _WITHDRAWAL_WORDS = {"withdrawal", "withdrawals", "debit", "dr"}

        for w in words:
            txt = w[4].strip().lower()
            cx  = (w[0] + w[2]) / 2
            # Skip words in the left 35% (Date/Description columns)
            if cx < page_width * 0.35:
                continue
            if txt in _DEPOSIT_WORDS and dep_hdr_x is None:
                dep_hdr_x = cx
                header_y  = w[1]
            elif txt in _WITHDRAWAL_WORDS and wdw_hdr_x is None:
                wdw_hdr_x = cx
                if header_y is None:
                    header_y = w[1]
            elif txt == "balance" and bal_hdr_x is None and cx > page_width * 0.5:
                bal_hdr_x = cx

        if dep_hdr_x is None and wdw_hdr_x is None:
            logger.info(
                "[HSBC-PRESCAN-V2] No deposit/withdrawal headers found. "
                "All words in right half: %s",
                [w[4].strip() for w in words if (w[0]+w[2])/2 > page_width*0.35][:20],
            )
            return empty_result  # no_table=True

        if dep_hdr_x is None:
            dep_hdr_x = page_width * 0.64
        if wdw_hdr_x is None:
            wdw_hdr_x = page_width * 0.76
        if bal_hdr_x is None:
            bal_hdr_x = page_width * 0.88

        dep_hdr_x = float(dep_hdr_x)
        wdw_hdr_x = float(wdw_hdr_x)
        bal_hdr_x = float(bal_hdr_x)

        def _amounts_balances_for_headers(
            h_y: float, d_x: float, w_x: float, b_x: float
        ) -> tuple[list[dict], list[dict]]:
            """Classify numeric words into Cr/Dr amounts and Balance column.

            When windows overlap (common on HSBC), assign each token to the column
            whose header x is *nearest* — otherwise B/F digits in the Balance
            column can be mis-tagged as Deposit (Cr) and never reach ``balances``.
            """
            min_y_local = h_y + 4
            dep_lo, dep_hi = d_x - 30.0, d_x + 30.0
            wdw_lo, wdw_hi = w_x - 30.0, w_x + 30.0
            bal_lo, bal_hi = b_x - 52.0, b_x + 48.0
            am: list[dict] = []
            bal: list[dict] = []
            for w in words:
                x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
                if y0 < min_y_local:
                    continue
                if not AMOUNT_RE.match(text):
                    continue
                x_mid = (x0 + x1) / 2
                if x_mid < page_width * 0.22:
                    continue
                amt_val = float(text.replace(",", ""))
                if text.isdigit() and 1900 <= amt_val <= 2100:
                    continue
                if text.isdigit() and 1 <= amt_val <= 31:
                    continue
                hits: list[tuple[str, float]] = []
                if dep_lo <= x_mid <= dep_hi:
                    hits.append(("Cr", d_x))
                if wdw_lo <= x_mid <= wdw_hi:
                    hits.append(("Dr", w_x))
                if bal_lo <= x_mid <= bal_hi:
                    hits.append(("Bal", b_x))
                if not hits and x_mid > wdw_hi + 12.0 and x_mid > page_width * 0.52:
                    hits.append(("Bal", b_x))
                if not hits:
                    continue
                best_kind, _best_x = min(hits, key=lambda h: abs(x_mid - h[1]))
                if best_kind == "Cr":
                    am.append({"y": y0, "col": "Cr", "amount": amt_val, "text": text})
                elif best_kind == "Dr":
                    am.append({"y": y0, "col": "Dr", "amount": amt_val, "text": text})
                else:
                    bal.append({"y": y0, "amount": amt_val})
            am.sort(key=lambda r: r["y"])
            bal.sort(key=lambda r: r["y"])
            return am, bal

        hy = float(header_y or 0.0)
        amounts, balances = _amounts_balances_for_headers(
            hy, dep_hdr_x, wdw_hdr_x, bal_hdr_x
        )

        # ── Detect account section headers ───────────────────────────────────
        # PyMuPDF stores long strings as separate words; reconstruct lines by
        # grouping words with similar y-values, then look for section strings.
        sections: list[dict] = []
        from collections import defaultdict as _dd
        line_words: dict[int, list] = _dd(list)
        for w in words:
            bucket = int(round(w[1] / 3.0)) * 3   # 3-pt grid
            line_words[bucket].append(w)

        for bucket in sorted(line_words):
            ws = sorted(line_words[bucket], key=lambda w: w[0])
            line_text = " ".join(w[4].strip() for w in ws)
            line_y    = ws[0][1]
            for sec in SECTION_STRINGS:
                if sec in line_text:
                    sections.append({"y": line_y, "header": sec})
                    break

        sections.sort(key=lambda r: r["y"])

        # Snap Deposit / Withdrawal / Balance to the *Account Activities* table row
        # below the first HKD section title. The first "Deposit" in PDF word order
        # can be Portfolio Summary; ``bal_hdr_x`` can then come from a different row
        # than ``dep_hdr_x``, so B/F digits miss the balance window entirely.
        if sections:
            y_sec_min = min(float(s["y"]) for s in sections)
            prev_hdr_y = float(header_y or 0.0)
            _hdr_band = 8.0
            deposit_candidates = [
                w for w in words
                if w[4].strip().lower() in _DEPOSIT_WORDS
                and (w[0] + w[2]) / 2 > page_width * 0.35
                and y_sec_min - 25.0 < w[1] < y_sec_min + 130.0
            ]
            if deposit_candidates:
                ref = min(deposit_candidates, key=lambda ww: ww[1])
                dep_hdr_x = (ref[0] + ref[2]) / 2.0
                header_y = ref[1]
                wdw_hdr_x_new: float | None = None
                bal_hdr_x_new: float | None = None
                for w in words:
                    if abs(w[1] - float(header_y)) > _hdr_band:
                        continue
                    cx = (w[0] + w[2]) / 2
                    if cx < page_width * 0.35:
                        continue
                    tl = w[4].strip().lower()
                    if tl in _WITHDRAWAL_WORDS and wdw_hdr_x_new is None:
                        wdw_hdr_x_new = cx
                    elif tl == "balance" and bal_hdr_x_new is None and cx > page_width * 0.5:
                        bal_hdr_x_new = cx
                if wdw_hdr_x_new is not None:
                    wdw_hdr_x = wdw_hdr_x_new
                if bal_hdr_x_new is not None:
                    bal_hdr_x = bal_hdr_x_new
                hy = float(header_y)
                amounts, balances = _amounts_balances_for_headers(
                    hy, dep_hdr_x, wdw_hdr_x, bal_hdr_x
                )
                logger.info(
                    "[HSBC-PRESCAN-V2] Account Activities anchor hy=%.1f (was %.1f) "
                    "bal_x=%.1f; amounts=%d balances=%d",
                    hy,
                    prev_hdr_y,
                    bal_hdr_x,
                    len(amounts),
                    len(balances),
                )

        # ── Detect date labels ("7 Nov", "1 Dec", "14 Jan", …) ──────────────
        # A date label is a 1-2 digit day number in the Date column, followed
        # by a month abbreviation on the same or next word at the same y.
        date_labels: list[dict] = []
        # Group words by y-bucket; look for pattern: digit(s) + month
        DATE_DAY_RE = _re.compile(r'^\d{1,2}$')
        processed_ys: set[int] = set()
        sorted_words = sorted(words, key=lambda w: (w[1], w[0]))
        for i, w in enumerate(sorted_words):
            y_bucket = int(round(w[1] / 3.0)) * 3
            if y_bucket in processed_ys:
                continue
            txt = w[4].strip()
            if DATE_DAY_RE.match(txt) and w[0] < page_width * 0.20:
                # Check next word for month abbrev at similar y
                for j in range(i + 1, min(i + 4, len(sorted_words))):
                    nw = sorted_words[j]
                    if abs(nw[1] - w[1]) > 8:   # too far vertically
                        break
                    ntxt = nw[4].strip().lower().rstrip(".")
                    if ntxt in MONTHS:
                        label = f"{txt} {nw[4].strip()}"
                        date_labels.append({"y": w[1], "label": label})
                        processed_ys.add(y_bucket)
                        break

        date_labels.sort(key=lambda r: r["y"])

        logger.debug(
            "[HSBC-PRESCAN-V2] amounts=%d (Cr=%d Dr=%d) balances=%d "
            "sections=%d dates=%d",
            len(amounts),
            sum(1 for a in amounts if a["col"] == "Cr"),
            sum(1 for a in amounts if a["col"] == "Dr"),
            len(balances), len(sections), len(date_labels),
        )
        return {
            "amounts": amounts, "balances": balances,
            "sections": sections, "date_labels": date_labels,
            "header_y": header_y or 0.0,
            "dep_hdr_x": dep_hdr_x, "wdw_hdr_x": wdw_hdr_x, "bal_hdr_x": bal_hdr_x,
            "page_height": page_height,
            "page_width": page_width,
            "no_table": False,
            # Raw words retained only for same-page P0 table-map enrichment
            # (not serialized / not logged). Callers may pop after enrich.
            "_words": words,
        }

    @staticmethod
    def _hsbc_v2_bf_opening_by_section(
        sections: List[dict],
        amounts: List[dict],
        balances: List[dict],
        section_for_y: Callable[[float], str],
        date_for_y: Callable[[float], str],
        label_to_date: Callable[[str], str],
        *,
        header_y: float = 0.0,
    ) -> Dict[str, Dict[str, Any]]:
        """Build one B/F (brought-forward) row per HSBC section that has Cr/Dr amounts.

        Opening lines appear in the PDF as balance-only rows (no Deposit/Withdrawal).
        Prescan records them in ``balances`` but not in ``amounts``, so V2 must
        synthesize a row before the first amount in each section.

        On continuation pages the B/F line can sit *above* the section title row.
        Using the section title y alone as the lower bound misses those balances; we use
        the table header (first section) or the last Cr/Dr row of prior sections.
        """
        _VALID_ACCT = {
            "HSBC Business Direct HKD Current",
            "HSBC Business Direct HKD Savings",
            "HSBC Business Direct Foreign Currency Savings",
        }
        bf_by_section: Dict[str, Dict[str, Any]] = {}
        for si, sec in enumerate(sections):
            header = sec["header"]
            if header not in _VALID_ACCT:
                continue
            y_sec = float(sec["y"])
            section_amounts = [
                a for a in amounts if section_for_y(float(a["y"])) == header
            ]
            if not section_amounts:
                continue
            y_first = min(float(a["y"]) for a in section_amounts)
            baseline = float(header_y or 0.0) + 4.0
            if si == 0:
                # Account title often sits above the Deposit/Withdrawal header row;
                # B/F can fall between them (y < baseline). Anchor below the higher
                # of those two baselines minus a hairline margin.
                y_lo = min(y_sec, baseline) - 2.0
            else:
                before_headers = {sections[j]["header"] for j in range(si)}
                prev_amt_ys = [
                    float(a["y"])
                    for a in amounts
                    if section_for_y(float(a["y"])) in before_headers
                ]
                y_lo = (
                    max(prev_amt_ys)
                    if prev_amt_ys
                    else baseline
                )
                # Last prior row and the next block's B/F can share nearby y;
                # nudge the floor up slightly less aggressively.
                y_lo -= 25.0
            if y_lo < 0.0:
                y_lo = 0.0
            cands = [
                b for b in balances
                if y_lo < float(b["y"]) < y_first
            ]
            if not cands and balances:
                # B/F can sit slightly above ``y_lo`` (title/header geometry); anything
                # below the column-header row is noise from another table.
                y_floor = max(0.0, float(header_y or 0.0) - 45.0)
                cands = [
                    b for b in balances
                    if y_floor < float(b["y"]) < y_first
                ]
            if not cands:
                continue
            b_open = max(cands, key=lambda b: float(b["y"]))
            dt_label = date_for_y(float(b_open["y"]))
            txn_date = label_to_date(dt_label)
            txn_currency = (
                "FCY"
                if header == "HSBC Business Direct Foreign Currency Savings"
                else "HKD"
            )
            bf_by_section[header] = {
                "transaction_date": txn_date or None,
                "value_date": None,
                "description": "B/F BALANCE 承前轉結",
                "deposit": None,
                "withdrawal": None,
                "balance": b_open["amount"],
                "currency": txn_currency,
                "account_type": header,
                "account_number": None,
                "categorise": "",
                "confidence_score": 1.0,
            }
        return bf_by_section

    @staticmethod
    def _hsbc_extract_descriptions(page, amounts: list, ps: dict) -> list:
        """Extract transaction descriptions for each prescan amount via PyMuPDF.

        For every amount in `amounts` (sorted by y, same as prescan output), this
        method collects all words that fall within:
          • Horizontal (x): the Transaction Details column
              left  = 8% of page width  (after date day number)
              right = dep_hdr_x − 35 pt (just before Deposit ±30pt window)
          • Vertical (y): an exclusive band for that transaction
              top   = amount_y − 2 pt
              bot   = min(amount_y + 22 pt, next_amount_y − 1 pt)
                      — 22 pt spans ≈2 lines of 11-pt text

        Words are sorted (y, x) to preserve reading order and joined with a space.

        Returns a list[str] the same length as `amounts` — one description per
        transaction. Empty string if no words were found for a row (indicates a
        scanned/image-only page where VLM fallback should be used instead).
        """
        if not amounts:
            return []

        words       = page.get_text("words")   # (x0, y0, x1, y1, text, blk, ln, wi)
        page_width  = page.rect.width
        dep_hdr_x   = ps["dep_hdr_x"]

        # Description column x-range
        desc_x_left  = page_width * 0.08          # after date column
        desc_x_right = dep_hdr_x - 35.0           # just before Deposit window

        # Build an exclusive y-band for each transaction so adjacent rows
        # never share a word.
        y_bands: list[tuple[float, float]] = []
        for i, amt in enumerate(amounts):
            y_top = amt["y"] - 2.0
            if i + 1 < len(amounts):
                y_bot = min(amt["y"] + 22.0, amounts[i + 1]["y"] - 1.0)
            else:
                y_bot = amt["y"] + 22.0
            y_bands.append((y_top, y_bot))

        # Bucket each word into the matching transaction band
        desc_words: list[list[tuple]] = [[] for _ in amounts]

        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
            if not text:
                continue
            x_mid = (x0 + x1) / 2
            if not (desc_x_left <= x_mid <= desc_x_right):
                continue
            for i, (y_top, y_bot) in enumerate(y_bands):
                if y_top <= y0 <= y_bot:
                    desc_words[i].append((y0, x0, text))
                    break

        # Sort words (y, x) → reading order; join into final description strings
        descriptions: list[str] = []
        for word_list in desc_words:
            word_list.sort(key=lambda t: (t[0], t[1]))
            descriptions.append(" ".join(t[2] for t in word_list))

        return descriptions

    @staticmethod
    def _hsbc_annotate_separator_lines(img_bgr, page, render_scale: float):
        """Annotate the rendered HSBC page image with a spreadsheet-like grid
        so the VLM can unambiguously identify each transaction row and the
        correct Deposit vs Withdrawal column for every amount.

        Two types of annotation are drawn:

        A) THREE VERTICAL COLUMN SEPARATORS (dark blue, thickness 2)
           Drawn from the column-header row to the bottom of the page:
             • Left edge of the Deposit column
             • Between Deposit and Withdrawal columns
             • Right edge of the Withdrawal column (= left of Balance)
           Effect: visually boxes each amount into exactly one column,
           eliminating Dr/Cr cross-reading.

        B) HORIZONTAL TRANSACTION-ROW SEPARATORS (medium gray, thickness 1)
           One thin line drawn just ABOVE every row that contains an amount in
           the Deposit or Withdrawal column, detected via PyMuPDF word positions.
           Effect: one visual cell per transaction — the VLM cannot merge
           consecutive rows even within the same date group.

        Args:
            img_bgr:       OpenCV BGR numpy array of the rendered page (3-channel).
            page:          fitz.Page for text-position extraction.
            render_scale:  render_dpi / 72  (PDF pts → pixel conversion factor).

        Returns a new BGR numpy array with the grid drawn in; original is unchanged.
        """
        import re as _re
        import cv2 as _cv2

        AMOUNT_RE = _re.compile(
            r'^\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$'   # 1,234.56 / 1,234
            r'|^\d{4,}(?:\.\d{1,2})?$'               # 12345.67
            r'|^\d+\.\d{2}$'                          # 0.22
        )

        words = page.get_text("words")   # (x0,y0,x1,y1,text,blk,ln,wi)
        if not words:
            return img_bgr.copy()

        page_width  = page.rect.width
        page_height = page.rect.height

        # ── Locate column header positions ────────────────────────────────────
        dep_hdr_x:  float | None = None
        wdw_hdr_x:  float | None = None
        bal_hdr_x:  float | None = None
        header_y:   float | None = None

        for w in words:
            txt = w[4].strip().lower()
            cx  = (w[0] + w[2]) / 2
            if txt == "deposit" and dep_hdr_x is None:
                dep_hdr_x = cx
                header_y  = w[1]
            elif txt == "withdrawal" and wdw_hdr_x is None:
                wdw_hdr_x = cx
            elif txt == "balance" and bal_hdr_x is None:
                bal_hdr_x = cx

        # Fallbacks for text-poor / partially-scanned pages (HSBC A4 proportions)
        if dep_hdr_x is None:
            dep_hdr_x = page_width * 0.64
        if wdw_hdr_x is None:
            wdw_hdr_x = page_width * 0.76
        if bal_hdr_x is None:
            bal_hdr_x = page_width * 0.88

        min_y = (header_y or 0) + 4   # ignore everything above the table header

        # ── Column boundary x-positions (PDF pts) ────────────────────────────
        # Half-width of each amount column; 48 pt covers typical HSBC column width
        col_half = 48.0
        dep_lo   = dep_hdr_x - col_half
        dep_hi   = dep_hdr_x + col_half
        wdw_lo   = wdw_hdr_x - col_half
        wdw_hi   = wdw_hdr_x + col_half

        # Three vertical separator x-positions (PDF pts):
        #   vline_A: left edge of Deposit column
        #   vline_B: midpoint between Deposit and Withdrawal (clear divider)
        #   vline_C: right edge of Withdrawal column (= left edge of Balance)
        vline_A = dep_lo                              # left of Deposit
        vline_B = (dep_hdr_x + wdw_hdr_x) / 2       # between Deposit & Withdrawal
        vline_C = wdw_hdr_x + col_half               # right of Withdrawal

        # ── Collect horizontal separator y-positions ──────────────────────────
        # Every word that is a monetary amount inside the Deposit or Withdrawal
        # column band gives us the top-y of that transaction row.
        raw_sep_ys: set[int] = set()
        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
            if y0 < min_y:
                continue
            if not AMOUNT_RE.match(text):
                continue
            x_mid = (x0 + x1) / 2
            # Skip Date column and reference column (extended to 22% for year numbers)
            if x_mid < page_width * 0.22:
                continue
            _ann_val = float(text.replace(",", ""))
            # Skip year-like numbers (e.g. "2025") — appear in FCY full dates
            if text.isdigit() and 1900 <= _ann_val <= 2100:
                continue
            # Skip bare day numbers 1–31 (FCY date day portions)
            if text.isdigit() and 1 <= _ann_val <= 31:
                continue
            if (dep_lo <= x_mid <= dep_hi) or (wdw_lo <= x_mid <= wdw_hi):
                # Bucket to nearest 2-pt grid to merge amounts on the same row
                raw_sep_ys.add(int(round(y0 / 2.0)) * 2)

        # ── Draw onto image ───────────────────────────────────────────────────
        img_out   = img_bgr.copy()
        img_h, img_w = img_out.shape[:2]

        def _pdf_x_to_px(pdf_x: float) -> int:
            return max(0, min(img_w - 1, int(pdf_x * render_scale)))

        def _pdf_y_to_px(pdf_y: float) -> int:
            return max(0, min(img_h - 1, int(pdf_y * render_scale)))

        # ── A) Vertical column separators ────────────────────────────────────
        header_px_y   = _pdf_y_to_px(header_y or 0)
        VCOL_COLOR    = (160, 60, 0)    # dark blue (BGR)
        VCOL_THICKNESS = 2

        for vx_pdf in (vline_A, vline_B, vline_C):
            px = _pdf_x_to_px(vx_pdf)
            _cv2.line(
                img_out,
                (px, header_px_y),
                (px, img_h - 1),
                VCOL_COLOR,
                VCOL_THICKNESS,
            )

        # ── B) Horizontal transaction-row separators ──────────────────────────
        txn_col_start_px = _pdf_x_to_px(page_width * 0.14)  # skip Date column
        HROW_COLOR       = (120, 120, 120)   # medium gray
        HROW_THICKNESS   = 1

        h_lines_drawn = 0
        for pdf_y in sorted(raw_sep_ys):
            # Place line 2 PDF-pts above the amount text (sits between rows)
            pixel_y = _pdf_y_to_px(pdf_y - 2)
            if pixel_y <= header_px_y:
                continue
            _cv2.line(
                img_out,
                (txn_col_start_px, pixel_y),
                (img_w - 1, pixel_y),
                HROW_COLOR,
                HROW_THICKNESS,
            )
            h_lines_drawn += 1

        logger.debug(
            "[HSBC-ANNOTATE] vertical separators at x=%.1f,%.1f,%.1f pt; "
            "%d horizontal row lines drawn",
            vline_A, vline_B, vline_C, h_lines_drawn,
        )
        return img_out

    def _hsbc_write_annotated_page_jpeg(
        self,
        page,
        page_num: int,
    ) -> tuple[str, str, dict[str, Any]]:
        """Render HSBC page to annotated JPEG; return temp path, pix hash, image_options."""
        import fitz
        import hashlib as _hashlib
        import numpy as _np
        import cv2 as _cv2
        import tempfile as _tempfile

        _hsbc_render_dpi = int(os.getenv("HSBC_RENDER_DPI", "300"))
        render_scale = _hsbc_render_dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(render_scale, render_scale),
            colorspace=fitz.csRGB,
        )
        page_hash = _hashlib.sha256(pix.samples).hexdigest()

        img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
            pix.height, pix.width, 3
        )
        img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)
        annotated = BankStatementParser._hsbc_annotate_separator_lines(
            img_bgr, page, render_scale
        )

        _save_debug = os.getenv("HSBC_SAVE_ANNOTATED", "").lower() in ("1", "true", "yes")
        if _save_debug:
            _debug_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "..", "hsbc_debug"
            )
            os.makedirs(_debug_dir, exist_ok=True)
            _debug_path = os.path.join(
                _debug_dir, f"hsbc_page{page_num + 1}_annotated.jpg"
            )
            _cv2.imwrite(_debug_path, annotated, [_cv2.IMWRITE_JPEG_QUALITY, 95])
            logger.info("[HSBC][P%d] Debug annotated image saved: %s", page_num + 1, _debug_path)

        tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp_path = tmp.name
        tmp.close()

        _hsbc_jpeg_q = int(os.getenv("HSBC_JPEG_QUALITY", "90"))
        _cv2.imwrite(tmp_path, annotated, [_cv2.IMWRITE_JPEG_QUALITY, _hsbc_jpeg_q])

        _hsbc_max_side = int(os.getenv("HSBC_MAX_SIDE", "2000"))
        image_opts: dict[str, Any] = {
            "max_side": _hsbc_max_side,
            "format": "JPEG",
            "quality": _hsbc_jpeg_q,
        }
        return tmp_path, page_hash, image_opts

    def _bea_write_page_jpeg_for_manager(
        self,
        page,
        page_num: int,
    ) -> tuple[str, str, dict[str, Any]]:
        """Render BEA page to JPEG for cross-VLM AR manager (no HSBC grid overlay)."""
        import fitz
        import hashlib as _hashlib
        import numpy as _np
        import cv2 as _cv2
        import tempfile as _tempfile

        _dpi = int(os.getenv("BEA_RENDER_DPI", os.getenv("HSBC_RENDER_DPI", "300")))
        render_scale = _dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(render_scale, render_scale),
            colorspace=fitz.csRGB,
        )
        page_hash = _hashlib.sha256(pix.samples).hexdigest()
        img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
            pix.height, pix.width, 3
        )
        img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)
        tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp_path = tmp.name
        tmp.close()
        _jpeg_q = int(os.getenv("BEA_JPEG_QUALITY", os.getenv("HSBC_JPEG_QUALITY", "90")))
        _cv2.imwrite(tmp_path, img_bgr, [_cv2.IMWRITE_JPEG_QUALITY, _jpeg_q])
        _max_side = int(os.getenv("BEA_MAX_SIDE", os.getenv("HSBC_MAX_SIDE", "2000")))
        image_opts: dict[str, Any] = {
            "max_side": _max_side,
            "format": "JPEG",
            "quality": _jpeg_q,
        }
        return tmp_path, page_hash, image_opts

    def _hang_seng_write_page_jpeg_for_manager(
        self,
        page,
        page_num: int,
    ) -> tuple[str, str, dict[str, Any]]:
        """Render Hang Seng page to JPEG for AR manager (plain page, no HSBC grid)."""
        import fitz
        import hashlib as _hashlib
        import numpy as _np
        import cv2 as _cv2
        import tempfile as _tempfile

        _dpi = int(
            os.getenv(
                "HANG_SENG_RENDER_DPI",
                os.getenv("BEA_RENDER_DPI", os.getenv("HSBC_RENDER_DPI", "300")),
            )
        )
        render_scale = _dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(render_scale, render_scale),
            colorspace=fitz.csRGB,
        )
        page_hash = _hashlib.sha256(pix.samples).hexdigest()
        img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
            pix.height, pix.width, 3
        )
        img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)
        tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp_path = tmp.name
        tmp.close()
        _jpeg_q = int(
            os.getenv(
                "HANG_SENG_JPEG_QUALITY",
                os.getenv("BEA_JPEG_QUALITY", os.getenv("HSBC_JPEG_QUALITY", "90")),
            )
        )
        _cv2.imwrite(tmp_path, img_bgr, [_cv2.IMWRITE_JPEG_QUALITY, _jpeg_q])
        _max_side = int(
            os.getenv(
                "HANG_SENG_MAX_SIDE",
                os.getenv("BEA_MAX_SIDE", os.getenv("HSBC_MAX_SIDE", "2000")),
            )
        )
        image_opts: dict[str, Any] = {
            "max_side": _max_side,
            "format": "JPEG",
            "quality": _jpeg_q,
        }
        return tmp_path, page_hash, image_opts

    def _ocbc_write_page_jpeg_for_manager(
        self,
        page,
        page_num: int,
    ) -> tuple[str, str, dict[str, Any]]:
        """Render OCBC page to JPEG for cross-VLM AR manager (plain page, no HSBC grid)."""
        import fitz
        import hashlib as _hashlib
        import numpy as _np
        import cv2 as _cv2
        import tempfile as _tempfile

        _dpi = int(
            os.getenv(
                "OCBC_RENDER_DPI",
                os.getenv("BEA_RENDER_DPI", os.getenv("HSBC_RENDER_DPI", "300")),
            )
        )
        render_scale = _dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(render_scale, render_scale),
            colorspace=fitz.csRGB,
        )
        page_hash = _hashlib.sha256(pix.samples).hexdigest()
        img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
            pix.height, pix.width, 3
        )
        img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)
        tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp_path = tmp.name
        tmp.close()
        _jpeg_q = int(
            os.getenv(
                "OCBC_JPEG_QUALITY",
                os.getenv("BEA_JPEG_QUALITY", os.getenv("HSBC_JPEG_QUALITY", "90")),
            )
        )
        _cv2.imwrite(tmp_path, img_bgr, [_cv2.IMWRITE_JPEG_QUALITY, _jpeg_q])
        _max_side = int(
            os.getenv(
                "OCBC_MAX_SIDE",
                os.getenv("BEA_MAX_SIDE", os.getenv("HSBC_MAX_SIDE", "2000")),
            )
        )
        image_opts: dict[str, Any] = {
            "max_side": _max_side,
            "format": "JPEG",
            "quality": _jpeg_q,
        }
        return tmp_path, page_hash, image_opts

    def _scb_write_page_jpeg_for_manager(
        self,
        page,
        page_num: int,
    ) -> tuple[str, str, dict[str, Any]]:
        """Render SCB page to JPEG for cross-VLM AR manager (plain page, no grid)."""
        import fitz
        import hashlib as _hashlib
        import numpy as _np
        import cv2 as _cv2
        import tempfile as _tempfile

        _dpi = int(
            os.getenv(
                "SCB_MANAGER_RENDER_DPI",
                os.getenv("SCB_V2_RENDER_DPI", os.getenv("HSBC_RENDER_DPI", "300")),
            )
        )
        render_scale = _dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(render_scale, render_scale),
            colorspace=fitz.csRGB,
        )
        page_hash = _hashlib.sha256(pix.samples).hexdigest()
        img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
            pix.height, pix.width, 3
        )
        img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)
        tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp_path = tmp.name
        tmp.close()
        _jpeg_q = int(
            os.getenv(
                "SCB_MANAGER_JPEG_QUALITY",
                os.getenv("SCB_V2_JPEG_QUALITY", os.getenv("HSBC_JPEG_QUALITY", "90")),
            )
        )
        _cv2.imwrite(tmp_path, img_bgr, [_cv2.IMWRITE_JPEG_QUALITY, _jpeg_q])
        _max_side = int(
            os.getenv(
                "SCB_MANAGER_MAX_SIDE",
                os.getenv("SCB_V2_MAX_SIDE", os.getenv("HSBC_MAX_SIDE", "2000")),
            )
        )
        image_opts: dict[str, Any] = {
            "max_side": _max_side,
            "format": "JPEG",
            "quality": _jpeg_q,
        }
        return tmp_path, page_hash, image_opts

    def _boc_write_page_jpeg_for_manager(
        self,
        page,
        page_num: int,
    ) -> tuple[str, str, dict[str, Any]]:
        """Render BOC page to JPEG for cross-VLM AR manager (plain page, no HSBC grid)."""
        import fitz
        import hashlib as _hashlib
        import numpy as _np
        import cv2 as _cv2
        import tempfile as _tempfile

        _dpi = int(
            os.getenv(
                "BOC_RENDER_DPI",
                os.getenv("BEA_RENDER_DPI", os.getenv("HSBC_RENDER_DPI", "300")),
            )
        )
        render_scale = _dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(render_scale, render_scale),
            colorspace=fitz.csRGB,
        )
        page_hash = _hashlib.sha256(pix.samples).hexdigest()
        img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
            pix.height, pix.width, 3
        )
        img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)
        tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp_path = tmp.name
        tmp.close()
        _jpeg_q = int(
            os.getenv(
                "BOC_JPEG_QUALITY",
                os.getenv("BEA_JPEG_QUALITY", os.getenv("HSBC_JPEG_QUALITY", "90")),
            )
        )
        _cv2.imwrite(tmp_path, img_bgr, [_cv2.IMWRITE_JPEG_QUALITY, _jpeg_q])
        _max_side = int(
            os.getenv(
                "BOC_MAX_SIDE",
                os.getenv("BEA_MAX_SIDE", os.getenv("HSBC_MAX_SIDE", "2000")),
            )
        )
        image_opts: dict[str, Any] = {
            "max_side": _max_side,
            "format": "JPEG",
            "quality": _jpeg_q,
        }
        return tmp_path, page_hash, image_opts

    def _bocom_write_page_jpeg_for_manager(
        self,
        page,
        page_num: int,
    ) -> tuple[str, str, dict[str, Any]]:
        """Render BOCOM page to JPEG for cross-VLM AR manager (plain page, no HSBC grid)."""
        import fitz
        import hashlib as _hashlib
        import numpy as _np
        import cv2 as _cv2
        import tempfile as _tempfile

        _dpi = int(
            os.getenv(
                "BOCOM_V2_RENDER_DPI",
                os.getenv(
                    "BOCOM_RENDER_DPI",
                    os.getenv("BEA_RENDER_DPI", os.getenv("HSBC_RENDER_DPI", "300")),
                ),
            )
        )
        render_scale = _dpi / 72.0
        pix = page.get_pixmap(
            matrix=fitz.Matrix(render_scale, render_scale),
            colorspace=fitz.csRGB,
        )
        page_hash = _hashlib.sha256(pix.samples).hexdigest()
        img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
            pix.height, pix.width, 3
        )
        img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)
        tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
        tmp_path = tmp.name
        tmp.close()
        _jpeg_q = int(
            os.getenv(
                "BOCOM_V2_JPEG_QUALITY",
                os.getenv(
                    "BOCOM_JPEG_QUALITY",
                    os.getenv("BEA_JPEG_QUALITY", os.getenv("HSBC_JPEG_QUALITY", "90")),
                ),
            )
        )
        _cv2.imwrite(tmp_path, img_bgr, [_cv2.IMWRITE_JPEG_QUALITY, _jpeg_q])
        _max_side = int(
            os.getenv(
                "BOCOM_V2_MAX_SIDE",
                os.getenv(
                    "BOCOM_MAX_SIDE",
                    os.getenv("BEA_MAX_SIDE", os.getenv("HSBC_MAX_SIDE", "2000")),
                ),
            )
        )
        image_opts: dict[str, Any] = {
            "max_side": _max_side,
            "format": "JPEG",
            "quality": _jpeg_q,
        }
        return tmp_path, page_hash, image_opts

    async def _hsbc_process_page(
        self,
        page,
        page_num: int,
        page_count: int,
        specific_prompt: str,
        default_prompt: str,
        vlm_model: str,
        company_identity: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """Process one HSBC page through the three-stage pipeline:
          1. Pre-scan  — count Deposit/Withdrawal amounts via PyMuPDF text positions
          2. Annotate  — draw separator lines at transaction-row boundaries (OpenCV)
          3. VLM loop  — extract transactions; retry with count-hint if mismatched
        """
        # ── Stage 1: Pre-scan ──────────────────────────────────────────────────
        dep_count, wdw_count, prescan_total = BankStatementParser._hsbc_prescan_count(page)
        logger.info(
            "[HSBC][P%d] Pre-scan: %d deposits + %d withdrawals = %d expected",
            page_num + 1, dep_count, wdw_count, prescan_total,
        )

        tmp_path, page_hash, image_opts = self._hsbc_write_annotated_page_jpeg(page, page_num)
        try:
            # ── Stage 3: VLM extraction + validation loop ──────────────────
            max_attempts  = max(1, int(os.getenv("HSBC_MAX_RETRIES", "3")))
            best_txns: List[Dict[str, Any]] = []
            # Manhattan distance of best_txns vs prescan (lower = better)
            best_dist: int = 10_000

            def _count_dep(txns: List[Dict[str, Any]]) -> int:
                """Count transactions that have a Deposit (Cr) amount."""
                return sum(
                    1 for t in txns
                    if t.get("存入") or t.get("deposit") or t.get("received")
                )

            def _count_wdw(txns: List[Dict[str, Any]]) -> int:
                """Count transactions that have a Withdrawal (Dr) amount."""
                return sum(
                    1 for t in txns
                    if t.get("提取") or t.get("withdrawal") or t.get("spent")
                )

            def _manhattan(txns: List[Dict[str, Any]]) -> int:
                """Manhattan distance between VLM Cr/Dr counts and prescan counts."""
                return abs(_count_dep(txns) - dep_count) + abs(_count_wdw(txns) - wdw_count)

            for attempt in range(1, max_attempts + 1):
                if attempt == 1:
                    prompt       = specific_prompt
                    attempt_hash = page_hash
                    track        = "HSBC-P"
                elif attempt == 2:
                    prev_dep = _count_dep(best_txns)
                    prev_wdw = _count_wdw(best_txns)
                    hint = (
                        "\n\n━━━ COUNT VERIFICATION (this page only) ━━━\n"
                        f"A text-position scan found {dep_count} Deposit (Cr) amount(s) "
                        f"and {wdw_count} Withdrawal (Dr) amount(s) on this page "
                        f"({prescan_total} transaction amounts total, excluding the B/F BALANCE "
                        f"row and 無交易 markers).\n"
                        f"Your previous extraction returned {prev_dep} deposit(s) and "
                        f"{prev_wdw} withdrawal(s). Please re-examine every row between the "
                        f"separator lines visible in the image and correct your output to match "
                        f"the expected {dep_count} deposit(s) and {wdw_count} withdrawal(s).\n"
                        f"IMPORTANT: If you cannot find a transaction that clearly appears in "
                        f"the image, do NOT invent one. It is better to return fewer rows than "
                        f"to fabricate an amount from a balance figure or surrounding text."
                    )
                    prompt       = specific_prompt + hint
                    attempt_hash = page_hash + ":r2"
                    track        = "HSBC-RETRY"
                else:
                    # Last resort: generic DEFAULT prompt
                    prompt       = default_prompt
                    attempt_hash = page_hash + ":r3"
                    track        = "HSBC-DEFAULT"

                txns      = await self._run_vlm_track(
                    tmp_path, prompt, attempt_hash,
                    vlm_model, track, company_identity,
                    max_tokens=8000,
                    image_options=image_opts,
                    filter_balance_anchor_rows=False,
                )
                vlm_dep   = _count_dep(txns)
                vlm_wdw   = _count_wdw(txns)
                dist      = abs(vlm_dep - dep_count) + abs(vlm_wdw - wdw_count)

                # ── Best-result selection: lowest Manhattan distance wins ────
                # Tie-break: more total transactions is better (catches all rows)
                if dist < best_dist or (
                    dist == best_dist
                    and (vlm_dep + vlm_wdw) > (_count_dep(best_txns) + _count_wdw(best_txns))
                ):
                    best_txns = txns
                    best_dist = dist

                # ── Validation ─────────────────────────────────────────────
                if prescan_total == 0:
                    # No pre-scan signal (cover page or prescan failed) → accept
                    logger.info(
                        "[HSBC][P%d] Attempt %d: pre-scan=0 (no column headers), "
                        "vlm dep=%d wdw=%d → accepting",
                        page_num + 1, attempt, vlm_dep, vlm_wdw,
                    )
                    break

                if vlm_dep == dep_count and vlm_wdw == wdw_count:
                    logger.info(
                        "[HSBC][P%d] Attempt %d/%d: prescan dep=%d wdw=%d, "
                        "vlm dep=%d wdw=%d → EXACT MATCH ✓",
                        page_num + 1, attempt, max_attempts,
                        dep_count, wdw_count, vlm_dep, vlm_wdw,
                    )
                    break
                else:
                    if attempt < max_attempts:
                        logger.warning(
                            "[HSBC][P%d] Attempt %d/%d: prescan dep=%d wdw=%d, "
                            "vlm dep=%d wdw=%d (dist=%d) → MISMATCH — retrying with hint",
                            page_num + 1, attempt, max_attempts,
                            dep_count, wdw_count, vlm_dep, vlm_wdw, dist,
                        )
                    else:
                        logger.warning(
                            "[HSBC][P%d] Attempt %d/%d: prescan dep=%d wdw=%d, "
                            "vlm dep=%d wdw=%d (dist=%d) → MISMATCH — "
                            "accepting best result (dist=%d)",
                            page_num + 1, attempt, max_attempts,
                            dep_count, wdw_count, vlm_dep, vlm_wdw, dist, best_dist,
                        )

        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        return best_txns

    # ─────────────────────────────────────────────────────────────────────────
    # V2 pipeline — prescan-driven: PyMuPDF supplies amounts, VLM reads text
    # ─────────────────────────────────────────────────────────────────────────

    async def _hsbc_process_page_v2(
        self,
        page,
        page_num: int,
        page_count: int,
        vlm_model: str,
        company_identity: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """Prescan-driven HSBC pipeline (V2).

        Stage 1 — Prescan     : PyMuPDF extracts amounts, Dr/Cr column, y-positions,
                                section headers, date labels, and balances.
                                P0: summary exclusion bands + HsbcTableMap enrichment.
        Stage 2a — PyMuPDF    : Extract descriptions from the text layer using the
                                known y-positions from Stage 1. Used for all digital
                                PDFs (>= 50% of transaction rows have description text).
                                No VLM call, no hallucination risk.
        Stage 2b — VLM fallback: If the page lacks a text layer (scanned/image PDF),
                                fall back to PROMPT_V2 for descriptions only.
                                P1: adaptive window crops when HSBC_WINDOW_VLM is on.
        Stage 3 — Merge       : Combine prescan amounts with Stage 2 descriptions,
                                plus date/section from prescan. Build final rows.
        """
        from app.services.hsbc_table_map import (
            apply_reversible_enhancement,
            assess_page_quality,
            build_hsbc_table_map,
            build_row_anchors,
            crop_window_bgr,
            enrich_prescan_with_table_map,
            plan_transaction_windows,
            window_vlm_enabled,
        )

        # ── Stage 1: Prescan + P0 table map ─────────────────────────────────
        ps = BankStatementParser._hsbc_prescan_amounts(page)
        _words = ps.pop("_words", None) or page.get_text("words")
        try:
            _page_text = page.get_text("text") or ""
        except Exception:
            _page_text = ""
        ps = enrich_prescan_with_table_map(
            ps,
            words=_words,
            page_number=page_num + 1,
            page_text=_page_text,
        )

        if ps["no_table"]:
            logger.info(
                "[HSBC-V2][P%d] No transaction table detected — skipping "
                "(classification=%s)",
                page_num + 1,
                ps.get("classification"),
            )
            return []

        # Skip pure legal/marketing pages even if a stray header matched
        if ps.get("classification") == "legal_or_marketing" and not ps.get("amounts"):
            logger.info(
                "[HSBC-V2][P%d] Legal/marketing page with no eligible amounts — skipping",
                page_num + 1,
            )
            return []

        amounts     = ps["amounts"]         # ground-truth Dr/Cr rows, sorted by y
        balances    = ps["balances"]        # balance entries sorted by y
        sections    = ps["sections"]        # section headers sorted by y
        date_labels = ps["date_labels"]     # date labels sorted by y
        page_height = ps["page_height"]
        page_width  = float(ps.get("page_width") or page.rect.width)

        header_ym = BankStatementParser._hsbc_header_year_month(page)
        if header_ym is None:
            logger.warning(
                "[HSBC-V2][P%d] Could not parse statement header date in top 30%% of page; "
                "using host-year sliding window for row date labels",
                page_num + 1,
            )

        logger.info(
            "[HSBC-V2][P%d] Prescan: %d amounts (Cr=%d, Dr=%d), %d balances, "
            "%d sections, %d dates, class=%s, excluded=%d",
            page_num + 1,
            len(amounts),
            sum(1 for a in amounts if a["col"] == "Cr"),
            sum(1 for a in amounts if a["col"] == "Dr"),
            len(balances),
            len(sections),
            len(date_labels),
            ps.get("classification"),
            len(ps.get("excluded_amounts") or []),
        )

        if not amounts and not sections:
            logger.warning("[HSBC-V2][P%d] No amounts or sections found — skipping", page_num + 1)
            return []

        # ── Stage 2a: PyMuPDF description extraction ─────────────────────────
        pymu_descs: list[str] | None = None
        fill_rate = 0.0

        if amounts:
            raw_descs = BankStatementParser._hsbc_extract_descriptions(page, amounts, ps)
            filled    = sum(1 for d in raw_descs if d.strip())
            fill_rate = filled / len(amounts)
            logger.info(
                "[HSBC-V2][P%d] PyMuPDF descriptions: %d/%d rows have text (%.0f%%)",
                page_num + 1, filled, len(amounts), fill_rate * 100,
            )
            # Use PyMuPDF if >= 50% of rows have at least one description word.
            # Below that threshold the page is likely a scanned image — use VLM.
            if fill_rate >= 0.50:
                pymu_descs = raw_descs
                logger.info("[HSBC-V2][P%d] Using PyMuPDF descriptions (digital PDF)", page_num + 1)

        # ── Stage 2b: VLM fallback (scanned / image-only pages) ──────────────
        vlm_rows: list[dict] = []
        failed_windows: list[str] = []

        if pymu_descs is None:
            logger.info("[HSBC-V2][P%d] PyMuPDF text sparse — falling back to VLM", page_num + 1)
            import fitz
            import numpy as _np
            import cv2 as _cv2
            import tempfile as _tempfile
            import json as _json
            import re as _re
            from app.bank_prompts.hsbc import PROMPT_V2 as _HSBC_PROMPT_V2

            _hsbc_render_dpi = int(os.getenv("HSBC_RENDER_DPI", "300"))
            render_scale = _hsbc_render_dpi / 72.0
            pix = page.get_pixmap(
                matrix=fitz.Matrix(render_scale, render_scale),
                colorspace=fitz.csRGB,
            )
            img_rgb   = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
                pix.height, pix.width, 3
            )
            img_bgr   = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)
            annotated = BankStatementParser._hsbc_annotate_separator_lines(
                img_bgr, page, render_scale
            )

            quality = assess_page_quality(
                annotated,
                has_text_layer=False,
                description_fill_rate=fill_rate,
            )
            annotated = apply_reversible_enhancement(
                annotated, quality.get("enhancement_recipe")
            )
            # Refresh table map quality / windows for observability
            anchors = build_row_anchors(
                amounts,
                page_number=page_num + 1,
                sections=sections,
                excluded_amounts=ps.get("excluded_amounts") or [],
            )
            windows = plan_transaction_windows(
                anchors,
                page_height=page_height,
                date_label_ys=[float(d.get("y", 0.0)) for d in date_labels],
            )
            from app.services.hsbc_table_map import ExclusionBand

            _bands = [
                ExclusionBand(
                    y0=float(b["y0"]),
                    y1=float(b["y1"]),
                    reason=str(b.get("reason") or ""),
                    source_text=str(b.get("source_text") or ""),
                    section_id=b.get("section_id"),
                    confidence=float(b.get("confidence") or 1.0),
                )
                for b in (ps.get("exclusion_bands") or [])
            ]
            ps["table_map"] = build_hsbc_table_map(
                page_number=page_num + 1,
                page_width=page_width,
                page_height=page_height,
                classification=ps.get("classification") or "unknown",
                header_y=float(ps.get("header_y") or 0.0),
                dep_hdr_x=float(ps.get("dep_hdr_x") or page_width * 0.64),
                wdw_hdr_x=float(ps.get("wdw_hdr_x") or page_width * 0.76),
                bal_hdr_x=float(ps.get("bal_hdr_x") or page_width * 0.88),
                sections=sections,
                exclusion_bands=_bands,
                row_anchors=anchors,
                windows=windows,
                quality=quality,
            ).to_dict()
            ps["windows"] = [w.to_dict() for w in windows]

            _save_debug = os.getenv("HSBC_SAVE_ANNOTATED", "").lower() in ("1", "true", "yes")
            if _save_debug:
                _debug_dir  = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "..", "..", "hsbc_debug"
                )
                os.makedirs(_debug_dir, exist_ok=True)
                _debug_path = os.path.join(_debug_dir, f"hsbc_v2_page{page_num + 1}_annotated.jpg")
                _cv2.imwrite(_debug_path, annotated, [_cv2.IMWRITE_JPEG_QUALITY, 95])
                logger.info("[HSBC-V2][P%d] Debug image saved: %s", page_num + 1, _debug_path)

            _hsbc_jpeg_q   = int(os.getenv("HSBC_JPEG_QUALITY", "90"))
            _hsbc_max_side = int(os.getenv("HSBC_MAX_SIDE", "2000"))
            image_opts     = {"max_side": _hsbc_max_side, "format": "JPEG", "quality": _hsbc_jpeg_q}

            async def _vlm_desc_from_bgr(_img, _track: str) -> list[dict]:
                tmp_path = ""
                try:
                    tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                    tmp_path = tmp.name
                    tmp.close()
                    _cv2.imwrite(tmp_path, _img, [_cv2.IMWRITE_JPEG_QUALITY, _hsbc_jpeg_q])
                    with open(tmp_path, "rb") as _imgf:
                        _v2_page_hash = hashlib.sha256(_imgf.read()).hexdigest()
                    page_text = await self._vlm_recognize_page_text(
                        tmp_path,
                        _HSBC_PROMPT_V2,
                        _v2_page_hash,
                        vlm_model,
                        _track,
                        max_tokens=8000,
                        image_options=image_opts,
                    )
                    raw_clean = page_text.strip()
                    raw_clean = _re.sub(r'^```[a-z]*\n?', '', raw_clean)
                    raw_clean = _re.sub(r'\n?```$', '', raw_clean).strip()
                    try:
                        parsed = _json.loads(raw_clean)
                        return list(parsed.get("rows") or [])
                    except _json.JSONDecodeError as je:
                        logger.warning(
                            "[HSBC-V2][P%d] VLM invalid JSON (%s) track=%s; raw=%.200s",
                            page_num + 1, je, _track, raw_clean,
                        )
                        return []
                finally:
                    if tmp_path and os.path.exists(tmp_path):
                        os.remove(tmp_path)

            try:
                use_windows = window_vlm_enabled() and len(windows) > 0 and len(amounts) > 0
                if use_windows:
                    logger.info(
                        "[HSBC-V2][P%d] Window-scoped VLM: %d window(s), profile=%s",
                        page_num + 1,
                        len(windows),
                        quality.get("render_profile"),
                    )
                    for win in windows:
                        crop = crop_window_bgr(
                            annotated,
                            y0_pt=win.y0,
                            y1_pt=win.y1,
                            render_scale=render_scale,
                            page_width_pt=page_width,
                            header_y_pt=float(ps.get("header_y") or 0.0) or None,
                        )
                        rows = await _vlm_desc_from_bgr(
                            crop, f"HSBC-V2-WIN:{win.window_id}"
                        )
                        if not rows:
                            failed_windows.append(win.window_id)
                            # One targeted retry on failure
                            rows = await _vlm_desc_from_bgr(
                                crop, f"HSBC-V2-WIN-RETRY:{win.window_id}"
                            )
                            if rows and win.window_id in failed_windows:
                                failed_windows.remove(win.window_id)
                            elif not rows:
                                logger.warning(
                                    "[HSBC-V2][P%d] Window failed after retry: %s",
                                    page_num + 1,
                                    win.window_id,
                                )
                        # Remap window-local y_pct into page y_pct using window bounds
                        win_h = max(1e-6, win.y1 - win.y0)
                        for r in rows:
                            try:
                                local_pct = float(r.get("y_pct", 50.0))
                            except (TypeError, ValueError):
                                local_pct = 50.0
                            page_y = win.y0 + (local_pct / 100.0) * win_h
                            r["y_pct"] = (page_y / page_height) * 100.0
                            r["_window_id"] = win.window_id
                            r["_expected_row_ids"] = list(win.expected_row_ids)
                        vlm_rows.extend(rows)
                else:
                    vlm_rows = await _vlm_desc_from_bgr(annotated, "HSBC-V2-DESC")
            except Exception as vlm_err:
                logger.error("[HSBC-V2][P%d] VLM call failed: %s", page_num + 1, vlm_err, exc_info=True)
                vlm_rows = []

            logger.info(
                "[HSBC-V2][P%d] VLM returned %d description rows (failed_windows=%d)",
                page_num + 1,
                len(vlm_rows),
                len(failed_windows),
            )

        # ── Stage 3: Merge ──────────────────────────────────────────────────
        # VLM y_pct helpers (only used when pymu_descs is None)
        def _vlm_y_to_pts(y_pct: float) -> float:
            return (float(y_pct) / 100.0) * page_height

        vlm_pts = [_vlm_y_to_pts(r.get("y_pct", 50)) for r in vlm_rows]

        # Max match distance for VLM fallback rows (~8% of page height)
        _MAX_MATCH_DIST_PTS = page_height * 0.08

        def _closest_vlm(y_pdf: float) -> dict | None:
            if not vlm_rows:
                return None
            best_idx  = min(range(len(vlm_rows)), key=lambda i: abs(vlm_pts[i] - y_pdf))
            best_dist = abs(vlm_pts[best_idx] - y_pdf)
            if best_dist > _MAX_MATCH_DIST_PTS:
                return None
            return vlm_rows[best_idx]

        def _section_for_y(y_pdf: float) -> str:
            """Return the section header in effect at y_pdf (last one above it)."""
            chosen = "HSBC Business Direct HKD Current"
            for s in sections:
                if s["y"] <= y_pdf:
                    chosen = s["header"]
                else:
                    break
            return chosen

        def _date_for_y(y_pdf: float) -> str:
            """Return the date label in effect at y_pdf (last one above it)."""
            chosen = ""
            for d in date_labels:
                if d["y"] <= y_pdf:
                    chosen = d["label"]
                else:
                    break
            return chosen

        def _balance_for_y(y_pdf: float) -> float | None:
            """Attach a printed Balance amount only when co-located with this row.

            HSBC prints balances on day-end (and B/F) lines only. Do not forward-fill
            a later day-end balance onto earlier same-day amount rows.
            """
            best_amt = None
            best_dy: float | None = None
            # Same-row y-band in PDF points (geometry tolerance, not a page/row index).
            row_band = 8.0
            for b in balances:
                try:
                    by = float(b["y"])
                    dy = abs(by - float(y_pdf))
                except (TypeError, ValueError, KeyError):
                    continue
                if dy <= row_band and (best_dy is None or dy < best_dy):
                    best_amt = b["amount"]
                    best_dy = dy
            return best_amt

        def _label_to_date(label: str) -> str:
            """Row label '7 Nov' → ISO using statement header (Y,M) when available."""
            if not label:
                return ""
            if header_ym is not None:
                y_h, m_h = header_ym
                iso = BankStatementParser._hsbc_partial_label_to_iso(label, y_h, m_h)
                if iso:
                    return iso
            return BankStatementParser._hsbc_label_to_date_sliding_window(label)

        bf_by_section = BankStatementParser._hsbc_v2_bf_opening_by_section(
            sections,
            amounts,
            balances,
            _section_for_y,
            _date_for_y,
            _label_to_date,
            header_y=float(ps.get("header_y") or 0.0),
        )
        if bf_by_section:
            logger.info(
                "[HSBC-V2][P%d] B/F opening rows for sections: %s",
                page_num + 1,
                ", ".join(sorted(bf_by_section.keys())),
            )

        # Build output — B/F opening row(s) then one transaction per prescan amount
        out_txns: List[Dict[str, Any]] = []
        _anchor_by_y: dict[float, dict] = {}
        for _a in (ps.get("row_anchors") or []):
            if _a.get("excluded"):
                continue
            try:
                _anchor_by_y[float(_a["y"])] = _a
            except (TypeError, ValueError, KeyError):
                continue

        emitted_bf_for_section: set[str] = set()
        for i, amt_rec in enumerate(amounts):
            y_pdf  = amt_rec["y"]
            acct_type_eff = _section_for_y(float(y_pdf))
            if acct_type_eff not in emitted_bf_for_section:
                bf_row = bf_by_section.get(acct_type_eff)
                if bf_row is not None:
                    out_txns.append(dict(bf_row))
                emitted_bf_for_section.add(acct_type_eff)

            col    = amt_rec["col"]     # "Cr" or "Dr"
            amount = amt_rec["amount"]

            # ── Description ──────────────────────────────────────────────
            window_id = None
            if pymu_descs is not None:
                # Primary path: direct PyMuPDF text — zero hallucination risk
                desc     = pymu_descs[i].strip()
                dt_label = _date_for_y(y_pdf)
                acct_type = _section_for_y(y_pdf)
            else:
                # Fallback path: VLM row matched by y-proximity
                closest   = _closest_vlm(y_pdf)
                desc      = closest.get("description", "").strip() if closest else ""
                dt_label  = closest.get("date_label", "").strip()  if closest else ""
                acct_type = closest.get("account_type", "").strip() if closest else ""
                window_id = closest.get("_window_id") if closest else None
                if not dt_label:
                    dt_label = _date_for_y(y_pdf)
                if not acct_type:
                    acct_type = _section_for_y(y_pdf)

            # Validate account_type
            _VALID_ACCT = {
                "HSBC Business Direct HKD Current",
                "HSBC Business Direct HKD Savings",
                "HSBC Business Direct Foreign Currency Savings",
            }
            if acct_type not in _VALID_ACCT:
                acct_type = _section_for_y(y_pdf)

            txn_date  = _label_to_date(dt_label)
            balance   = _balance_for_y(y_pdf)

            # FCY sections may hold non-HKD amounts — label currency accordingly.
            # We cannot determine the exact foreign currency from the text layer alone,
            # so we use "FCY" as a placeholder for Foreign Currency Savings rows.
            txn_currency = (
                "FCY"
                if acct_type == "HSBC Business Direct Foreign Currency Savings"
                else "HKD"
            )

            _anchor = _anchor_by_y.get(float(y_pdf))
            _row_id = (_anchor or {}).get("row_id")
            _sec_id = (_anchor or {}).get("section_id")
            _col_prov = {
                "deposit": "prescan_cr" if col == "Cr" else None,
                "withdrawal": "prescan_dr" if col == "Dr" else None,
                "balance": "prescan_balance_band" if balance is not None else None,
            }
            _token_ids = [
                f"{_row_id}:{col}:{amount}",
            ]
            if balance is not None:
                _token_ids.append(f"{_row_id}:Bal:{balance}")
            txn: Dict[str, Any] = {
                "transaction_date": txn_date or None,
                "value_date":       None,
                "description":      desc or "",
                "deposit":          amount if col == "Cr" else None,
                "withdrawal":       amount if col == "Dr" else None,
                "balance":          balance,
                "currency":         txn_currency,
                "account_type":     acct_type,
                "account_number":   None,
                "categorise":       "",
                "confidence_score": 0.85,
                "_hsbc_row_id":     _row_id,
                "_hsbc_section_id": _sec_id,
                "_hsbc_classification": ps.get("classification"),
                "parser_adapter":   "hsbc_adapter_v2",
                "source_page":      page_num + 1,
                "section_id":       _sec_id,
                "row_anchor_id":    _row_id,
                "numeric_token_ids": _token_ids,
                "column_provenance": _col_prov,
            }
            if balance is None:
                # Expected HSBC layout: Balance column blank on non-day-end rows.
                txn["balance_missing_expected"] = True
            if window_id:
                txn["_hsbc_window_id"] = window_id
            if window_id and window_id in failed_windows:
                txn["needs_review"] = True
                txn["_hsbc_window_failed"] = True
            from app.services.hsbc_contracts import apply_contracts_to_row

            txn = apply_contracts_to_row(
                txn,
                tokens=[
                    {
                        "column": col,
                        "band": "deposit" if col == "Cr" else "withdrawal",
                        "amount": amount,
                    }
                ],
            )
            out_txns.append(txn)

        # Contract A: activity page with anchors must emit rows
        from app.services.hsbc_contracts import validate_contract_a_coverage

        _cov = validate_contract_a_coverage(
            has_txn_header=not bool(ps.get("no_table")),
            amount_anchor_count=len(amounts),
            emitted_row_count=sum(
                1
                for t in out_txns
                if t.get("deposit") is not None or t.get("withdrawal") is not None
            ),
        )
        if not _cov.ok:
            logger.warning(
                "[HSBC-V2][P%d] Contract A coverage_failed: %s",
                page_num + 1,
                _cov.flags,
            )
            for t in out_txns:
                flags = list(t.get("validation_flags") or [])
                for f in _cov.flags:
                    if f not in flags:
                        flags.append(f)
                t["validation_flags"] = flags
                t["needs_review"] = True
                t["_contracts_ok"] = False

        # ── Emit empty 無交易 rows for sections with no amounts ──────────────
        sections_with_amounts: set[str] = set()
        for amt_rec in amounts:
            sections_with_amounts.add(_section_for_y(amt_rec["y"]))

        for sec in sections:
            if sec["header"] not in sections_with_amounts:
                # Get balance from prescan — nearest balance below this section
                sec_balance = _balance_for_y(sec["y"])
                empty_txn: Dict[str, Any] = {
                    "transaction_date": None,
                    "value_date":       None,
                    "description":      "無交易",
                    "deposit":          None,
                    "withdrawal":       None,
                    "balance":          sec_balance,
                    "currency":         "HKD",
                    "account_type":     sec["header"],
                    "account_number":   None,
                    "categorise":       "",
                    "confidence_score": 1.0,
                }
                out_txns.append(empty_txn)

        logger.info(
            "[HSBC-V2][P%d] Merged %d transactions (%d empty-section rows)",
            page_num + 1, len(out_txns),
            sum(1 for t in out_txns if t["description"] == "無交易"),
        )
        return out_txns

    # ─────────────────────────────────────────────────────────────────────────
    # BEA (Bank of East Asia) — prescan + description merge (HSBC V2–style)
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _bea_label_to_iso(label: str, header_ym: tuple[int, int] | None) -> str:
        """Parse BEA date labels: DDMMMYY, DD/MM/YYYY, DD-MM-YYYY, or partial day+month."""
        import datetime as _dt
        import re as _re

        if not label:
            return ""
        raw = label.strip()
        compact = raw.replace(" ", "").upper()
        _MON3 = {
            "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
            "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
        }
        m_compact = _re.match(r"^(\d{1,2})([A-Z]{3})(\d{2,4})$", compact)
        if m_compact:
            try:
                day = int(m_compact.group(1))
                mon = _MON3.get(m_compact.group(2), 0)
                yr = int(m_compact.group(3))
                if not mon:
                    return ""
                if yr < 100:
                    yr += 2000 if yr < 70 else 1900
                return _dt.date(yr, mon, day).isoformat()
            except (ValueError, OverflowError):
                return ""
        compact_slash = raw.replace(" ", "")
        m = _re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$", compact_slash)
        if m:
            day, mon, yr = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if yr < 100:
                yr += 2000 if yr < 70 else 1900
            try:
                return _dt.date(yr, mon, day).isoformat()
            except ValueError:
                return ""
        if header_ym is not None:
            y_h, m_h = header_ym
            iso = BankStatementParser._hsbc_partial_label_to_iso(raw, y_h, m_h)
            if iso:
                return iso
        return BankStatementParser._hsbc_label_to_date_sliding_window(raw)

    @staticmethod
    def _bea_norm_desc_for_match(desc: str) -> str:
        return " ".join(str(desc or "").lower().split())

    @staticmethod
    def _bea_is_total_transaction_summary(desc: str) -> bool:
        """BEA per-account subtotal row — not a transaction."""
        s = BankStatementParser._bea_norm_desc_for_match(desc)
        raw = str(desc or "")
        if "total transaction amount" in s:
            return True
        # 交易總金額
        if "\u4ea4\u6613\u7e3d\u91d1\u984d" in raw:
            return True
        # 交易筆數 / 交易笔数 + NO.OF TRANSACTION — period count line, not a monetary txn
        if "\u4ea4\u6613\u7b46\u6578" in raw or "\u4ea4\u6613\u7b14\u6570" in raw:
            return True
        if "no.oftransaction" in s.replace(" ", ""):
            return True
        return False

    @staticmethod
    def _bea_has_activity_section_title(page_text: str) -> bool:
        """True if page shows a real account-activities section header (not portfolio-only)."""
        import re as _re

        if not page_text or len(page_text.strip()) < 8:
            return False
        t = page_text
        if _re.search(
            r"HKD\s+CURRENT\s+ACCOUNT|STATEMENT\s+SAVINGS\s+ACCOUNT",
            t,
            _re.IGNORECASE,
        ):
            return True
        if "\u6e2f\u5143\u5f80\u4f86\u8cec\u6236" in t:  # 港元往來����
            return True
        if "\u5132\u84c4(\u7d50\u55ae)\u8cec\u6236" in t:  # ���)����
            return True
        if "\u5f80\u4f86\u8cec\u6236" in t and "HKD" in t.upper():
            return True
        return False

    @staticmethod
    def _bea_is_cover_like_portfolio_page(page_text: str) -> bool:
        """Portfolio / cover page — no transaction table should be extracted."""
        if not page_text:
            return False
        u = page_text.upper()
        markers = (
            "PORTFOLIO SUMMARY",
            "ACCOUNT PORTFOLIO",
            "\u8ca1\u52d9\u7d44\u5408\u6458\u8981",  # ��務組合摘要
            "\u8cec\u6236\u7d44\u5408",  #�組合
        )
        if not any(m in page_text if ord(m[0]) > 127 else m in u for m in markers):
            return False
        if BankStatementParser._bea_has_activity_section_title(page_text):
            return False
        if BankStatementParser._bea_has_deposit_withdrawal_headers(page_text):
            return False
        return True

    @staticmethod
    def _hang_seng_is_cover_like_portfolio_page(page_text: str) -> bool:
        """Portfolio / cover page — skip VLM (same idea as BEA; Hang Seng–specific markers)."""
        if not page_text:
            return False
        u = page_text.upper()
        markers_en = (
            "PORTFOLIO SUMMARY",
            "ACCOUNT PORTFOLIO",
            "STATEMENT SUMMARY",
            "SUMMARY OF ACCOUNTS",
        )
        markers_zh = (
            "\u8ca1\u52d9\u7d44\u5408\u6458\u8981",  # 財務組合摘要
            "\u8cec\u6236\u7d44\u5408",  # 賬戶組合
            "\u6236\u53e3\u7e3d\u89bd",  # 戶口總覽
            "\u7d9c\u5408\u6236\u53e3",  # 綜合戶口
        )
        hit_zh = any(m in page_text for m in markers_zh)
        hit_en = any(m in u for m in markers_en)
        if not hit_zh and not hit_en:
            return False
        if BankStatementParser._bea_has_deposit_withdrawal_headers(page_text):
            return False
        return True

    @staticmethod
    def _ocbc_is_cover_like_portfolio_page(page_text: str) -> bool:
        """OCBC summary/cover page — no ACCOUNT ACTIVITIES; skip VLM (saves cost, avoids noise)."""
        if not page_text:
            return False
        u = page_text.upper()
        if "OCBC" not in u and "華僑銀行" not in page_text:
            return False
        markers = ("PORTFOLIO SUMMARY", "ACCOUNT SUMMARY")
        if not any(m in u for m in markers):
            return False
        if "ACCOUNT ACTIVITIES" in u:
            return False
        if BankStatementParser._bea_has_deposit_withdrawal_headers(page_text):
            return False
        return True

    @staticmethod
    def _bea_has_deposit_withdrawal_headers(page_text: str) -> bool:
        """Printed activity table has both credit and debit column headers."""
        if not page_text:
            return False
        u = page_text.upper()
        credit_side = "DEPOSIT" in u or "CREDIT" in u
        debit_side = "WITHDRAWAL" in u or "DEBIT" in u
        zh = "\u5b58\u5165" in page_text and "\u652f\u51fa" in page_text
        return zh or (credit_side and debit_side)

    @staticmethod
    def _bea_find_information_footer_y(page) -> float | None:
        """Minimum y of INFORMATION / 資料 footer heading; amounts below are ignored."""
        import re as _re

        try:
            blocks = page.get_text("blocks") or []
        except Exception:
            blocks = []
        best: float | None = None
        for b in blocks:
            if len(b) < 5:
                continue
            x0, y0, x1, y1, txt = b[0], b[1], b[2], b[3], (b[4] or "").strip()
            if not txt or y1 - y0 < 4:
                continue
            first_line = txt.split("\n")[0].strip()
            if first_line.upper() in ("INFORMATION",) or first_line in (
                "\u8cc7\u6599",
            ):  # 資料
                if best is None or y0 < best:
                    best = float(y0)
            elif _re.match(
                r"^(INFORMATION|資料)\s*(\(|：|:|\.)",
                first_line,
                _re.IGNORECASE,
            ):
                if best is None or y0 < best:
                    best = float(y0)
        return best

    @staticmethod
    def _bea_normalise_account_header(raw: str) -> str:
        """Map BEA printed section titles to stable account_type labels."""
        import re as _re

        s = " ".join(raw.split())
        u = s.upper()
        if _re.search(r"HKD\s+CURRENT\s+ACCOUNT|\u6e2f\u5143\u5f80\u4f86", s):
            return "HKD CURRENT"
        if _re.search(
            r"STATEMENT\s+SAVINGS\s+ACCOUNT|\u5132\u84c4\s*\(\s*\u7d50\u55ae\s*\)",
            u + s,
        ):
            return "HKD STATEMENT SAVINGS"
        if "CORPORATEPLUS" in u.replace(" ", "") or "\u4f01\u696d\u7d9c\u5408\u7406\u8ca1" in s:
            return s[:120] if len(s) <= 120 else s[:117] + "..."
        return s[:120] if len(s) <= 120 else s[:117] + "..."

    @staticmethod
    def _bea_is_portfolio_only_line(line_text: str) -> bool:
        """Exclude portfolio-summary lines from section headers."""
        lt = line_text.strip()
        u = lt.upper()
        if "PORTFOLIO SUMMARY" in u or "\u8ca1\u52d9\u7d44\u5408\u6458\u8981" in lt:
            return True
        if "ACCOUNT PORTFOLIO" in u and "STATEMENT" not in u:
            return True
        if "\u8cec\u6236\u7d44\u5408" in lt and "\u5f80\u4f86" not in lt and "\u5132\u84c4" not in lt:
            if "HKD CURRENT" not in u and "SAVINGS" not in u:
                return True
        return False

    @staticmethod
    def _bea_post_filter_transactions(
        txns: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Dual-track BEA: drop subtotal/boilerplate rows; forward-fill account_type."""
        last_acct: str | None = None
        out: List[Dict[str, Any]] = []
        for t in txns:
            desc = str(
                t.get("description")
                or t.get("memo")
                or t.get("\u5099\u8a3b")
                or ""
            )
            if BankStatementParser._bea_is_total_transaction_summary(desc):
                continue
            row = dict(t)
            acct = str(
                row.get("account_type")
                or row.get("\u5e33\u6236\u985e\u578b")
                or row.get("\u8cec\u6236\u985e\u578b")
                or row.get("\u8d26\u6237\u7c7b\u578b")
                or ""
            ).strip()
            if not acct and last_acct:
                row["account_type"] = last_acct
                row["帳戶類型"] = last_acct
                row["賬戶類型"] = last_acct
            elif acct:
                last_acct = acct
            out.append(row)
        return out

    @staticmethod
    def _bea_forward_fill_transaction_dates(txns: List[Dict[str, Any]]) -> None:
        """Carry last non-empty transaction_date onto rows with blank date (BEA multi-line rows)."""
        last: str | None = None
        for row in txns:
            if str(row.get("description") or "") == "無交易":
                continue
            d = row.get("transaction_date")
            if d:
                last = str(d)
            elif last:
                row["transaction_date"] = last

    @staticmethod
    def _bea_prescan_amounts(page) -> dict:
        """PyMuPDF prescan for BEA HK statements (withdrawal/deposit/balance columns)."""
        import re as _re
        from collections import defaultdict as _dd

        AMOUNT_RE = _re.compile(
            r"^\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$"
            r"|^\d{4,}(?:\.\d{1,2})?$"
            r"|^\d+\.\d{2}$"
        )
        DATE_SLASH_RE = _re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})$")
        BEA_COMPACT_DATE_RE = _re.compile(r"^(\d{1,2})([A-Z]{3})(\d{2,4})$")
        MONTHS = {
            "jan", "feb", "mar", "apr", "may", "jun",
            "jul", "aug", "sep", "oct", "nov", "dec",
        }
        _DEPOSIT_WORDS = {
            "deposit", "deposits", "credit", "cr",
            "\u5b58\u5165",  # 存入
        }
        _WITHDRAWAL_WORDS = {
            "withdrawal", "withdrawals", "debit", "dr",
            "\u652f\u51fa",  # 支出
        }
        _BALANCE_WORDS = {
            "balance", "balances",
            "\u7d50\u9918", "\u4f59\u989d",
        }

        words = page.get_text("words")
        page_width = page.rect.width
        page_height = page.rect.height
        empty_result = {
            "amounts": [], "balances": [], "sections": [], "date_labels": [],
            "header_y": 0.0, "dep_hdr_x": page_width * 0.62,
            "wdw_hdr_x": page_width * 0.74, "bal_hdr_x": page_width * 0.88,
            "page_height": page_height, "no_table": True,
        }
        if not words:
            return empty_result

        full_text = page.get_text("text") or ""
        if BankStatementParser._bea_is_cover_like_portfolio_page(full_text):
            logger.info("[BEA-PRESCAN-V2] Portfolio/cover page — no_table")
            return empty_result

        info_y = BankStatementParser._bea_find_information_footer_y(page)
        _INFO_FOOTER_MARGIN = 8.0

        dep_hdr_x: float | None = None
        wdw_hdr_x: float | None = None
        bal_hdr_x: float | None = None
        header_y: float | None = None

        for w in words:
            txt = w[4].strip().lower()
            cx = (w[0] + w[2]) / 2
            if cx < page_width * 0.30:
                continue
            if txt in _DEPOSIT_WORDS and dep_hdr_x is None:
                dep_hdr_x = cx
                header_y = w[1]
            elif txt in _WITHDRAWAL_WORDS and wdw_hdr_x is None:
                wdw_hdr_x = cx
                if header_y is None:
                    header_y = w[1]
            elif txt in _BALANCE_WORDS and bal_hdr_x is None and cx > page_width * 0.48:
                bal_hdr_x = cx
                if header_y is None:
                    header_y = w[1]

        if dep_hdr_x is None or wdw_hdr_x is None:
            logger.info(
                "[BEA-PRESCAN-V2] Missing dep/wdw headers (dep=%s wdw=%s)",
                dep_hdr_x, wdw_hdr_x,
            )
            return empty_result

        if bal_hdr_x is None:
            bal_hdr_x = page_width * 0.88
        dep_hdr_x = float(dep_hdr_x)
        wdw_hdr_x = float(wdw_hdr_x)
        bal_hdr_x = float(bal_hdr_x)
        hy = float(header_y or 0.0)

        def _amounts_balances_for_headers(
            h_y: float, d_x: float, w_x: float, b_x: float
        ) -> tuple[list[dict], list[dict]]:
            min_y_local = h_y + 4
            dep_lo, dep_hi = d_x - 32.0, d_x + 32.0
            wdw_lo, wdw_hi = w_x - 32.0, w_x + 32.0
            bal_lo, bal_hi = b_x - 52.0, b_x + 48.0
            am: list[dict] = []
            bal: list[dict] = []
            for w in words:
                x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
                if y0 < min_y_local:
                    continue
                if not AMOUNT_RE.match(text):
                    continue
                x_mid = (x0 + x1) / 2
                if x_mid < page_width * 0.20:
                    continue
                amt_val = float(text.replace(",", ""))
                if text.isdigit() and 1900 <= amt_val <= 2100:
                    continue
                if text.isdigit() and 1 <= amt_val <= 31:
                    continue
                hits: list[tuple[str, float]] = []
                if dep_lo <= x_mid <= dep_hi:
                    hits.append(("Cr", d_x))
                if wdw_lo <= x_mid <= wdw_hi:
                    hits.append(("Dr", w_x))
                if bal_lo <= x_mid <= bal_hi:
                    hits.append(("Bal", b_x))
                if not hits and x_mid > max(dep_hi, wdw_hi) + 8.0 and x_mid > page_width * 0.50:
                    hits.append(("Bal", b_x))
                if not hits:
                    continue
                best_kind, _best_x = min(hits, key=lambda h: abs(x_mid - h[1]))
                if best_kind == "Cr":
                    am.append({"y": y0, "col": "Cr", "amount": amt_val, "text": text})
                elif best_kind == "Dr":
                    am.append({"y": y0, "col": "Dr", "amount": amt_val, "text": text})
                else:
                    bal.append({"y": y0, "amount": amt_val})
            am.sort(key=lambda r: r["y"])
            bal.sort(key=lambda r: r["y"])
            return am, bal

        amounts, balances = _amounts_balances_for_headers(hy, dep_hdr_x, wdw_hdr_x, bal_hdr_x)

        if info_y is not None:
            cutoff = float(info_y) - _INFO_FOOTER_MARGIN
            amounts = [a for a in amounts if float(a["y"]) < cutoff]
            balances = [b for b in balances if float(b["y"]) < cutoff]

        line_words: dict[int, list] = _dd(list)
        for w in words:
            bucket = int(round(w[1] / 3.0)) * 3
            line_words[bucket].append(w)

        sections: list[dict] = []
        _sec_re = _re.compile(
            r"(savings|current|integrated|business|commercial|往來|儲蓄|綜合)",
            _re.IGNORECASE,
        )
        for bucket in sorted(line_words):
            ws = sorted(line_words[bucket], key=lambda ww: ww[0])
            line_text = " ".join(ww[4].strip() for ww in ws)
            line_y = ws[0][1]
            lt = line_text.strip()
            if len(lt) < 6 or len(lt) > 140:
                continue
            if BankStatementParser._bea_is_portfolio_only_line(lt):
                continue
            if _sec_re.search(lt) and "BANK OF EAST ASIA" not in lt.upper():
                nh = BankStatementParser._bea_normalise_account_header(lt)
                sections.append({"y": line_y, "header": nh})
            elif "BANK OF EAST ASIA" in lt.upper() and len(lt) < 90:
                sections.append({"y": line_y, "header": "BEA Hong Kong"})

        sections.sort(key=lambda r: r["y"])
        if not sections:
            sections = [{"y": hy + 2.0, "header": "BEA Account"}]

        date_labels: list[dict] = []
        sorted_words = sorted(words, key=lambda w: (w[1], w[0]))
        processed_ys: set[int] = set()
        DATE_DAY_RE = _re.compile(r"^\d{1,2}$")
        for i, w in enumerate(sorted_words):
            y_bucket = int(round(w[1] / 3.0)) * 3
            if y_bucket in processed_ys:
                continue
            txt = w[4].strip()
            x_mid = (w[0] + w[2]) / 2
            if x_mid < page_width * 0.34 and y_bucket > hy - 2 and DATE_SLASH_RE.match(txt):
                date_labels.append({"y": w[1], "label": txt})
                processed_ys.add(y_bucket)
                continue
            txt_nospace_u = "".join(txt.split()).upper()
            if (
                w[1] > hy
                and x_mid < page_width * 0.42
                and BEA_COMPACT_DATE_RE.match(txt_nospace_u)
            ):
                date_labels.append({"y": w[1], "label": txt.strip()})
                processed_ys.add(y_bucket)
                continue
            if DATE_DAY_RE.match(txt) and x_mid < page_width * 0.22 and w[1] > hy:
                for j in range(i + 1, min(i + 4, len(sorted_words))):
                    nw = sorted_words[j]
                    if abs(nw[1] - w[1]) > 8:
                        break
                    ntxt = nw[4].strip().lower().rstrip(".")
                    if ntxt in MONTHS:
                        label = f"{txt} {nw[4].strip()}"
                        date_labels.append({"y": w[1], "label": label})
                        processed_ys.add(y_bucket)
                        break

        date_labels.sort(key=lambda r: r["y"])

        logger.debug(
            "[BEA-PRESCAN-V2] amounts=%d balances=%d sections=%d dates=%d",
            len(amounts), len(balances), len(sections), len(date_labels),
        )
        return {
            "amounts": amounts,
            "balances": balances,
            "sections": sections,
            "date_labels": date_labels,
            "header_y": hy,
            "dep_hdr_x": dep_hdr_x,
            "wdw_hdr_x": wdw_hdr_x,
            "bal_hdr_x": bal_hdr_x,
            "page_height": page_height,
            "no_table": False,
        }

    @staticmethod
    def _bea_extract_descriptions(page, amounts: list, ps: dict) -> list:
        """Description column text for each prescan amount (digital PDFs)."""
        if not amounts:
            return []

        words = page.get_text("words")
        page_width = page.rect.width
        dep_hdr_x = float(ps["dep_hdr_x"])
        wdw_hdr_x = float(ps["wdw_hdr_x"])
        amt_col_left = min(dep_hdr_x, wdw_hdr_x)
        desc_x_right = amt_col_left - 28.0
        desc_x_left = page_width * 0.20
        if desc_x_right <= desc_x_left + 15.0:
            desc_x_right = amt_col_left - 18.0
        if desc_x_right <= desc_x_left + 10.0:
            desc_x_left = page_width * 0.14

        y_bands: list[tuple[float, float]] = []
        for i, amt in enumerate(amounts):
            y_top = amt["y"] - 2.0
            if i + 1 < len(amounts):
                y_bot = min(amt["y"] + 26.0, amounts[i + 1]["y"] - 1.0)
            else:
                y_bot = amt["y"] + 26.0
            y_bands.append((y_top, y_bot))

        desc_words: list[list[tuple]] = [[] for _ in amounts]
        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
            if not text:
                continue
            x_mid = (x0 + x1) / 2
            if not (desc_x_left <= x_mid <= desc_x_right):
                continue
            for i, (y_top, y_bot) in enumerate(y_bands):
                if y_top <= y0 <= y_bot:
                    desc_words[i].append((y0, x0, text))
                    break

        descriptions: list[str] = []
        for word_list in desc_words:
            word_list.sort(key=lambda t: (t[0], t[1]))
            descriptions.append(" ".join(t[2] for t in word_list))
        return descriptions

    @staticmethod
    def _bea_annotate_separator_lines(img_bgr, page, render_scale: float, ps: dict):
        """Draw BEA amount-column guides + row separators for VLM description pass."""
        import re as _re
        import cv2 as _cv2

        AMOUNT_RE = _re.compile(
            r"^\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$"
            r"|^\d{4,}(?:\.\d{1,2})?$"
            r"|^\d+\.\d{2}$"
        )
        words = page.get_text("words")
        if not words:
            return img_bgr.copy()

        page_width = page.rect.width
        dep_hdr_x = float(ps["dep_hdr_x"])
        wdw_hdr_x = float(ps["wdw_hdr_x"])
        header_y = float(ps.get("header_y") or 0.0)
        col_half = 48.0
        dep_lo, dep_hi = dep_hdr_x - col_half, dep_hdr_x + col_half
        wdw_lo, wdw_hi = wdw_hdr_x - col_half, wdw_hdr_x + col_half
        left_amt = min(dep_lo, wdw_lo)
        mid_amt = (dep_hdr_x + wdw_hdr_x) / 2.0
        right_amt = max(dep_hi, wdw_hi)

        min_y = header_y + 4.0
        raw_sep_ys: set[int] = set()
        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
            if y0 < min_y:
                continue
            if not AMOUNT_RE.match(text):
                continue
            x_mid = (x0 + x1) / 2
            if x_mid < page_width * 0.18:
                continue
            _v = float(text.replace(",", ""))
            if text.isdigit() and 1900 <= _v <= 2100:
                continue
            if text.isdigit() and 1 <= _v <= 31:
                continue
            if (dep_lo <= x_mid <= dep_hi) or (wdw_lo <= x_mid <= wdw_hi):
                raw_sep_ys.add(int(round(y0 / 2.0)) * 2)

        img_out = img_bgr.copy()
        img_h, img_w = img_out.shape[:2]

        def _pdf_x_to_px(pdf_x: float) -> int:
            return max(0, min(img_w - 1, int(pdf_x * render_scale)))

        def _pdf_y_to_px(pdf_y: float) -> int:
            return max(0, min(img_h - 1, int(pdf_y * render_scale)))

        header_px_y = _pdf_y_to_px(header_y)
        VCOL_COLOR = (160, 60, 0)
        for vx_pdf in (left_amt, mid_amt, right_amt):
            px = _pdf_x_to_px(vx_pdf)
            _cv2.line(img_out, (px, header_px_y), (px, img_h - 1), VCOL_COLOR, 2)

        txn_col_start_px = _pdf_x_to_px(page_width * 0.12)
        HROW_COLOR = (120, 120, 120)
        for pdf_y in sorted(raw_sep_ys):
            pixel_y = _pdf_y_to_px(pdf_y - 2)
            if pixel_y <= header_px_y:
                continue
            _cv2.line(
                img_out, (txn_col_start_px, pixel_y), (img_w - 1, pixel_y),
                HROW_COLOR, 1,
            )
        return img_out

    @staticmethod
    def _bea_v2_bf_opening_by_section(
        sections: List[dict],
        amounts: List[dict],
        balances: List[dict],
        section_for_y: Callable[[float], str],
        date_for_y: Callable[[float], str],
        label_to_date: Callable[[str], str],
        *,
        header_y: float = 0.0,
    ) -> Dict[str, Dict[str, Any]]:
        """Synthesize B/F rows from balance-only lines before first Cr/Dr in each section."""
        bf_by_section: Dict[str, Dict[str, Any]] = {}
        for si, sec in enumerate(sections):
            header = sec["header"]
            if not str(header).strip():
                continue
            y_sec = float(sec["y"])
            section_amounts = [
                a for a in amounts if section_for_y(float(a["y"])) == header
            ]
            if not section_amounts:
                continue
            y_first = min(float(a["y"]) for a in section_amounts)
            baseline = float(header_y or 0.0) + 4.0
            if si == 0:
                y_lo = min(y_sec, baseline) - 2.0
            else:
                before_headers = {sections[j]["header"] for j in range(si)}
                prev_amt_ys = [
                    float(a["y"])
                    for a in amounts
                    if section_for_y(float(a["y"])) in before_headers
                ]
                y_lo = max(prev_amt_ys) if prev_amt_ys else baseline
                y_lo -= 25.0
            if y_lo < 0.0:
                y_lo = 0.0
            cands = [b for b in balances if y_lo < float(b["y"]) < y_first]
            if not cands and balances:
                y_floor = max(0.0, float(header_y or 0.0) - 45.0)
                cands = [b for b in balances if y_floor < float(b["y"]) < y_first]
            if not cands:
                continue
            b_open = max(cands, key=lambda b: float(b["y"]))
            dt_label = date_for_y(float(b_open["y"]))
            txn_date = label_to_date(dt_label)
            bf_by_section[header] = {
                "transaction_date": txn_date or None,
                "value_date": None,
                "description": "B/F BALANCE",
                "deposit": None,
                "withdrawal": None,
                "balance": b_open["amount"],
                "currency": "HKD",
                "account_type": header,
                "account_number": None,
                "categorise": "",
                "confidence_score": 1.0,
            }
        return bf_by_section

    async def _bea_process_page_v2(
        self,
        page,
        page_num: int,
        page_count: int,
        vlm_model: str,
        company_identity: Dict[str, Any] | None = None,
        carried_account: str | None = None,
    ) -> tuple[List[Dict[str, Any]], str | None]:
        """BEA prescan + PyMuPDF / VLM descriptions + merge.

        Returns (transactions, last_explicit_activity_header_or_None).
        """
        ps = BankStatementParser._bea_prescan_amounts(page)
        if ps["no_table"]:
            logger.info("[BEA-V2][P%d] No transaction table — skip", page_num + 1)
            return [], None

        amounts = ps["amounts"]
        balances = ps["balances"]
        sections_raw = list(ps["sections"])
        last_explicit: str | None = None
        for s in sections_raw:
            lab = str(s.get("header") or "")
            if lab in ("BEA Account", "BEA Hong Kong"):
                continue
            last_explicit = lab
        sections: list[dict] = []
        for s in sections_raw:
            h = str(s.get("header") or "")
            y = float(s["y"])
            if carried_account and h in ("BEA Account", "BEA Hong Kong"):
                sections.append({"y": y, "header": carried_account})
            else:
                sections.append({"y": y, "header": h})
        date_labels = ps["date_labels"]
        page_height = ps["page_height"]
        header_ym = BankStatementParser._hsbc_header_year_month(page)

        logger.info(
            "[BEA-V2][P%d] Prescan: %d amounts (Cr=%d Dr=%d), %d balances",
            page_num + 1,
            len(amounts),
            sum(1 for a in amounts if a["col"] == "Cr"),
            sum(1 for a in amounts if a["col"] == "Dr"),
            len(balances),
        )

        if not amounts and not sections:
            logger.warning("[BEA-V2][P%d] No amounts — skip", page_num + 1)
            return [], last_explicit

        pymu_descs: list[str] | None = None
        if amounts:
            raw_descs = BankStatementParser._bea_extract_descriptions(page, amounts, ps)
            filled = sum(1 for d in raw_descs if d.strip())
            fill_rate = filled / len(amounts)
            logger.info(
                "[BEA-V2][P%d] PyMuPDF descriptions: %d/%d (%.0f%%)",
                page_num + 1, filled, len(amounts), fill_rate * 100,
            )
            if fill_rate >= 0.50:
                pymu_descs = raw_descs

        vlm_rows: list[dict] = []
        if pymu_descs is None:
            logger.info("[BEA-V2][P%d] Sparse text — VLM description pass", page_num + 1)
            import fitz
            import json as _json
            import re as _re
            import tempfile as _tempfile
            import numpy as _np
            import cv2 as _cv2
            from app.bank_prompts.bea import PROMPT_V2 as _BEA_PROMPT_V2

            _dpi = int(os.getenv("BEA_RENDER_DPI", os.getenv("HSBC_RENDER_DPI", "300")))
            render_scale = _dpi / 72.0
            pix = page.get_pixmap(
                matrix=fitz.Matrix(render_scale, render_scale),
                colorspace=fitz.csRGB,
            )
            img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
                pix.height, pix.width, 3
            )
            img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)
            annotated = BankStatementParser._bea_annotate_separator_lines(
                img_bgr, page, render_scale, ps
            )
            _jpeg_q = int(os.getenv("BEA_JPEG_QUALITY", os.getenv("HSBC_JPEG_QUALITY", "90")))
            _max_side = int(os.getenv("BEA_MAX_SIDE", os.getenv("HSBC_MAX_SIDE", "2000")))
            image_opts = {"max_side": _max_side, "format": "JPEG", "quality": _jpeg_q}
            tmp_path = ""
            try:
                tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tmp_path = tmp.name
                tmp.close()
                _cv2.imwrite(tmp_path, annotated, [_cv2.IMWRITE_JPEG_QUALITY, _jpeg_q])
                with open(tmp_path, "rb") as _imgf:
                    _h = hashlib.sha256(_imgf.read()).hexdigest()
                page_text = await self._vlm_recognize_page_text(
                    tmp_path,
                    _BEA_PROMPT_V2,
                    _h,
                    vlm_model,
                    "BEA-V2-DESC",
                    max_tokens=8000,
                    image_options=image_opts,
                )
                raw_clean = _re.sub(r"^```[a-z]*\n?", "", page_text.strip())
                raw_clean = _re.sub(r"\n?```$", "", raw_clean).strip()
                try:
                    vlm_rows = _json.loads(raw_clean).get("rows", [])
                except _json.JSONDecodeError:
                    vlm_rows = []
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass

        def _vlm_y_to_pts(y_pct: float) -> float:
            return (float(y_pct) / 100.0) * page_height

        vlm_pts = [_vlm_y_to_pts(float(r.get("y_pct", 50))) for r in vlm_rows]
        _MAX_MATCH_DIST = page_height * 0.08

        def _closest_vlm(y_pdf: float) -> dict | None:
            if not vlm_rows:
                return None
            best_i = min(range(len(vlm_rows)), key=lambda i: abs(vlm_pts[i] - y_pdf))
            if abs(vlm_pts[best_i] - y_pdf) > _MAX_MATCH_DIST:
                return None
            return vlm_rows[best_i]

        def _section_for_y(y_pdf: float) -> str:
            chosen = sections[0]["header"] if sections else "BEA Account"
            for s in sections:
                if s["y"] <= y_pdf:
                    chosen = s["header"]
                else:
                    break
            return chosen

        def _date_for_y(y_pdf: float) -> str:
            chosen = ""
            for d in date_labels:
                if d["y"] <= y_pdf:
                    chosen = d["label"]
                else:
                    break
            return chosen

        def _balance_for_y(y_pdf: float) -> float | None:
            for b in balances:
                if b["y"] >= y_pdf - 5.0:
                    return b["amount"]
            return None

        def _label_to_date(label: str) -> str:
            return BankStatementParser._bea_label_to_iso(label, header_ym)

        bf_by_section = BankStatementParser._bea_v2_bf_opening_by_section(
            sections,
            amounts,
            balances,
            _section_for_y,
            _date_for_y,
            _label_to_date,
            header_y=float(ps.get("header_y") or 0.0),
        )

        out_txns: List[Dict[str, Any]] = []
        emitted_bf: set[str] = set()
        for i, amt_rec in enumerate(amounts):
            y_pdf = amt_rec["y"]
            acct_eff = _section_for_y(float(y_pdf))
            if acct_eff not in emitted_bf:
                bf_row = bf_by_section.get(acct_eff)
                if bf_row is not None:
                    out_txns.append(dict(bf_row))
                emitted_bf.add(acct_eff)

            col = amt_rec["col"]
            amount = amt_rec["amount"]
            if pymu_descs is not None:
                desc = pymu_descs[i].strip()
                dt_label = _date_for_y(y_pdf)
                acct_type = acct_eff
            else:
                closest = _closest_vlm(y_pdf)
                desc = (closest.get("description", "").strip() if closest else "")
                dt_label = (closest.get("date_label", "").strip() if closest else "")
                acct_type = (closest.get("account_type", "").strip() if closest else "")
                if not dt_label:
                    dt_label = _date_for_y(y_pdf)
                if not acct_type:
                    acct_type = acct_eff

            txn_date = _label_to_date(dt_label)
            balance = _balance_for_y(y_pdf)
            if BankStatementParser._bea_is_total_transaction_summary(desc):
                continue
            out_txns.append({
                "transaction_date": txn_date or None,
                "value_date": None,
                "description": desc or "",
                "deposit": amount if col == "Cr" else None,
                "withdrawal": amount if col == "Dr" else None,
                "balance": balance,
                "currency": "HKD",
                "account_type": acct_type,
                "account_number": None,
                "categorise": "",
                "confidence_score": 0.88,
            })

        # Placeholder 無交易 rows: only when this page has real Cr/Dr lines (not portfolio cover).
        sections_with_amounts = {_section_for_y(float(a["y"])) for a in amounts}
        if amounts:
            for sec in sections:
                if sec["header"] not in sections_with_amounts:
                    sb = _balance_for_y(sec["y"])
                    out_txns.append({
                        "transaction_date": None,
                        "value_date": None,
                        "description": "無交易",
                        "deposit": None,
                        "withdrawal": None,
                        "balance": sb,
                        "currency": "HKD",
                        "account_type": sec["header"],
                        "account_number": None,
                        "categorise": "",
                        "confidence_score": 1.0,
                    })

        BankStatementParser._bea_forward_fill_transaction_dates(out_txns)

        logger.info("[BEA-V2][P%d] Merged %d rows", page_num + 1, len(out_txns))
        return out_txns, last_explicit

    async def _parse_bea_statement(
        self,
        file_path: str,
        full_text: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        page_verification_out: Dict[int, str] | None = None,
    ) -> List[Dict]:
        """Parse BEA HK statements: prescan-driven V2 when text layer is usable."""
        import fitz

        from app.ocr.runtime import BANK_VLM_MODEL, ocr_service as _ocr_service
        from app.bank_prompts import BANK_PROMPT_DATABASE

        if not _ocr_service:
            logger.error("[BEA] OCR service not available")
            return []
        if not BANK_PROMPT_DATABASE.get("BEA"):
            return await self._parse_with_ocr_fallback(
                file_path, "BEA",
                company_identity=company_identity,
                progress_callback=progress_callback,
                page_verification_out=page_verification_out,
            )

        doc = fitz.open(file_path)
        page_count = len(doc)
        total_words = sum(len(doc[i].get_text("words") or []) for i in range(page_count))
        if total_words < 50:
            logger.info("[BEA] Low text layer (%d words) — dual-track VLM", total_words)
            doc.close()
            return await self._parse_with_ocr_fallback(
                file_path, "BEA",
                company_identity=company_identity,
                progress_callback=progress_callback,
                page_verification_out=page_verification_out,
            )

        all_txns: List[Dict] = []
        _use_v2 = os.getenv("BEA_PIPELINE_V2", "true").lower() in ("1", "true", "yes")
        logger.info(
            "[BEA] Starting pipeline: pages=%d model=%s v2=%s",
            page_count, BANK_VLM_MODEL, _use_v2,
        )
        self._emit_progress(
            progress_callback,
            percent=20, label="BEA解析中",
            page_current=0, page_total=page_count,
        )

        if not _use_v2:
            doc.close()
            return await self._parse_with_ocr_fallback(
                file_path, "BEA",
                company_identity=company_identity,
                progress_callback=progress_callback,
                page_verification_out=page_verification_out,
            )

        try:
            carried: str | None = None
            for page_num in range(page_count):
                page = doc[page_num]
                page_txns, last_exp = await self._bea_process_page_v2(
                    page,
                    page_num,
                    page_count,
                    BANK_VLM_MODEL,
                    company_identity,
                    carried_account=carried,
                )
                page_txns = await self._bea_apply_ar_manager_if_enabled(
                    page,
                    page_num,
                    page_txns,
                    page_verification_out,
                    company_identity,
                )
                if last_exp:
                    carried = last_exp
                for txn in page_txns:
                    txn["_page"] = page_num + 1
                all_txns.extend(page_txns)
        finally:
            try:
                doc.close()
            except Exception:
                pass

        return all_txns

    async def _parse_hsbc_statement(
        self,
        file_path: str,
        full_text: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        page_verification_out: Dict[int, str] | None = None,
    ) -> List[Dict]:
        """Parse HSBC Business Direct (HK) statements via the HSBC-specific pipeline.

        Three-stage process per page (HSBC only — no other bank uses this path):
          1. Pre-scan   — count Deposit/Withdrawal amounts via PyMuPDF word positions.
          2. Annotate   — draw horizontal separator lines between transaction rows
                          using OpenCV so the VLM has clear visual row boundaries.
          3. VLM loop   — extract; validate against pre-scan count; retry with an
                          explicit count hint if the counts do not match (≤ 3 attempts).
        """
        import fitz

        from app.ocr.runtime import BANK_VLM_MODEL, ocr_service as _ocr_service
        from app.bank_prompts import BANK_PROMPT_DATABASE

        if not _ocr_service:
            logger.error("[HSBC] OCR service not available")
            return []

        specific_prompt = BANK_PROMPT_DATABASE.get("HSBC") or ""
        default_prompt  = BANK_PROMPT_DATABASE["DEFAULT"]

        if not specific_prompt:
            logger.warning(
                "[HSBC] HSBC prompt missing — falling back to generic pipeline"
            )
            return await self._parse_with_ocr_fallback(
                file_path, "HSBC",
                company_identity=company_identity,
                progress_callback=progress_callback,
                page_verification_out=page_verification_out,
            )

        doc        = fitz.open(file_path)
        page_count = len(doc)
        all_txns: List[Dict] = []

        logger.info(
            "[HSBC] Starting HSBC-specific pipeline: %d pages, model=%s",
            page_count, BANK_VLM_MODEL,
        )
        self._emit_progress(
            progress_callback,
            percent=20, label="HSBC VLM 處理中",
            page_current=0, page_total=page_count,
        )

        _use_v2 = os.getenv("HSBC_PIPELINE_V2", "false").lower() in ("1", "true", "yes")
        logger.info("[HSBC] Pipeline version: %s", "V2 (prescan-driven)" if _use_v2 else "V1 (VLM-driven)")

        for page_num in range(page_count):
            page = doc[page_num]
            logger.info("[HSBC] Processing page %d/%d", page_num + 1, page_count)
            try:
                if _use_v2:
                    _word_count = len(page.get_text("words"))
                    _SCANNED_THRESHOLD = 20
                    if _word_count < _SCANNED_THRESHOLD:
                        logger.info(
                            "[HSBC-V2][P%d] Only %d words detected (<%d) — "
                            "scanned page, falling back to V1 VLM pipeline",
                            page_num + 1, _word_count, _SCANNED_THRESHOLD,
                        )
                        page_txns = await self._hsbc_process_page(
                            page=page,
                            page_num=page_num,
                            page_count=page_count,
                            specific_prompt=specific_prompt,
                            default_prompt=default_prompt,
                            vlm_model=BANK_VLM_MODEL,
                            company_identity=company_identity,
                        )
                    else:
                        page_txns = await self._hsbc_process_page_v2(
                            page=page,
                            page_num=page_num,
                            page_count=page_count,
                            vlm_model=BANK_VLM_MODEL,
                            company_identity=company_identity,
                        )
                else:
                    page_txns = await self._hsbc_process_page(
                        page=page,
                        page_num=page_num,
                        page_count=page_count,
                        specific_prompt=specific_prompt,
                        default_prompt=default_prompt,
                        vlm_model=BANK_VLM_MODEL,
                        company_identity=company_identity,
                    )
                page_txns = await self._hsbc_apply_ar_manager_if_enabled(
                    page,
                    page_num,
                    page_txns,
                    page_verification_out,
                    company_identity,
                )
                for txn in page_txns:
                    txn["_page"] = page_num + 1

                if page_txns:
                    logger.info(
                        "✅ [HSBC] Page %d: %d transactions", page_num + 1, len(page_txns)
                    )
                else:
                    logger.warning("⚠️ [HSBC] Page %d: no transactions", page_num + 1)

                all_txns.extend(page_txns)

            except Exception as page_err:
                logger.error(
                    "[HSBC] Page %d error: %s", page_num + 1, page_err, exc_info=True
                )

            progress_pct = min(95, 20 + int(((page_num + 1) / max(page_count, 1)) * 70))
            self._emit_progress(
                progress_callback,
                percent=progress_pct,
                label=f"HSBC VLM 處理中（第 {page_num + 1}/{page_count} 頁完成）",
                page_current=page_num + 1,
                page_total=page_count,
            )

        if _use_v2 and not all_txns:
            logger.warning(
                "[HSBC-V2] V2 found 0 transactions across %d pages — "
                "falling back to full V1 VLM pipeline for entire document",
                page_count,
            )
            for page_num in range(page_count):
                page = doc[page_num]
                logger.info("[HSBC-V1-FALLBACK] Processing page %d/%d", page_num + 1, page_count)
                try:
                    page_txns = await self._hsbc_process_page(
                        page=page,
                        page_num=page_num,
                        page_count=page_count,
                        specific_prompt=specific_prompt,
                        default_prompt=default_prompt,
                        vlm_model=BANK_VLM_MODEL,
                        company_identity=company_identity,
                    )
                    page_txns = await self._hsbc_apply_ar_manager_if_enabled(
                        page,
                        page_num,
                        page_txns,
                        page_verification_out,
                        company_identity,
                    )
                    for txn in page_txns:
                        txn["_page"] = page_num + 1
                    all_txns.extend(page_txns)
                except Exception as page_err:
                    logger.error(
                        "[HSBC-V1-FALLBACK] Page %d error: %s",
                        page_num + 1, page_err, exc_info=True,
                    )

        return all_txns

    async def _parse_hang_seng_statement(
        self,
        file_path: str,
        full_text: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        page_verification_out: Dict[int, str] | None = None,
    ) -> List[Dict]:
        """Parse Hang Seng via dual-track VLM (bank-specific prompt + AR manager when enabled)."""
        logger.info("[HANG_SENG] Dual-track VLM pipeline (hang_seng prompt)")
        return await self._parse_with_ocr_fallback(
            file_path,
            "HANG_SENG",
            company_identity=company_identity,
            progress_callback=progress_callback,
            page_verification_out=page_verification_out,
        )
    
    async def _parse_scb_statement(
        self,
        file_path: str,
        full_text: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        page_verification_out: Dict[int, str] | None = None,
    ) -> List[Dict]:
        """Parse Standard Chartered (HK) statements.

        V2 (prescan-driven):  PyMuPDF extracts amounts + descriptions; VLM only for scans.
        V1 (table-parser):    PyMuPDF find_tables() → VLM chunked fallback.

        SC 5-column layout: DATE | DESCRIPTION | DEBIT | CREDIT | BALANCE
        """
        import fitz
        doc = fitz.open(file_path)

        _use_v2 = os.getenv("SCB_PIPELINE_V2", "false").lower() in ("1", "true", "yes")
        logger.info(
            "[SCB] Pipeline version: %s, pages: %d",
            "V2 (prescan-driven)" if _use_v2 else "V1 (table-parser)",
            len(doc),
        )

        if _use_v2:
            from app.ocr.runtime import BANK_VLM_MODEL
            all_txns: List[Dict] = []
            for page_num in range(len(doc)):
                page = doc[page_num]
                logger.info("[SCB] Processing page %d/%d (V2)", page_num + 1, len(doc))
                self._emit_progress(
                    progress_callback,
                    percent=min(80, 20 + int(((page_num + 1) / max(len(doc), 1)) * 60)),
                    label=f"SCB V2 處理中（第 {page_num + 1}/{len(doc)} 頁）",
                    page_current=page_num + 1,
                    page_total=len(doc),
                )
                try:
                    page_txns = await self._scb_process_page_v2(
                        page=page,
                        page_num=page_num,
                        page_count=len(doc),
                        vlm_model=BANK_VLM_MODEL,
                        company_identity=company_identity,
                    )
                    if page_txns:
                        page_txns = await self._scb_apply_ar_manager_if_enabled(
                            page,
                            page_num,
                            page_txns,
                            page_verification_out,
                            company_identity,
                        )
                    for txn in page_txns:
                        txn["_page"] = page_num + 1
                    if page_txns:
                        logger.info("✅ [SCB-V2] Page %d: %d transactions", page_num + 1, len(page_txns))
                    else:
                        logger.warning("⚠️ [SCB-V2] Page %d: no transactions", page_num + 1)
                    all_txns.extend(page_txns)
                except Exception as page_err:
                    logger.error("[SCB-V2] Page %d error: %s", page_num + 1, page_err, exc_info=True)
            if all_txns:
                return all_txns
            logger.warning(
                "[SCB-V2] V2 found 0 transactions across %d pages — falling back to V1 pipeline",
                len(doc),
            )

        transactions = []

        logger.info(f"Starting SCB statement parsing: {file_path}")
        logger.info(f"Document has {len(doc)} pages")

        _scb_skip_keywords = {
            'date', 'debit', 'credit', 'balance', 'description',
            'opening balance', 'closing balance', 'brought forward',
            'carried forward', 'b/f', 'c/f', 'sub-total', 'total',
            'balance b/f', 'balance c/f',
        }

        for page_num, page in enumerate(doc):
            logger.info(f"Processing page {page_num + 1}/{len(doc)}")
            self._emit_progress(
                progress_callback,
                percent=min(80, 20 + int(((page_num + 1) / max(len(doc), 1)) * 60)),
                label=f"表格解析中（第 {page_num + 1}/{len(doc)} 頁）",
                page_current=page_num + 1,
                page_total=len(doc),
            )
            page_text_hint = page.get_text() or ""

            if "presented cheques" in page_text_hint.lower() or "by cheque no" in page_text_hint.lower():
                logger.info(f"[SCB] Page {page_num + 1}: Presented Cheques page — skipping (not transactions)")
                continue

            inferred_account_type = self._infer_scb_account_type_from_text(page_text_hint)

            tables_list = []

            # Strategy 1: Strict line detection
            try:
                tables = page.find_tables(
                    vertical_strategy="lines_strict",
                    horizontal_strategy="lines_strict",
                    snap_tolerance=5,
                )
                tables_list = list(tables) if tables else []
                logger.info(f"Page {page_num + 1}: Strict strategy found {len(tables_list)} table(s)")
            except Exception as e:
                logger.warning(f"Strict table detection failed: {e}")

            # Strategy 2: Less-strict lines
            if not tables_list:
                try:
                    tables = page.find_tables(
                        vertical_strategy="lines",
                        horizontal_strategy="lines",
                        snap_tolerance=10,
                    )
                    tables_list = list(tables) if tables else []
                    logger.info(f"Page {page_num + 1}: Lines strategy found {len(tables_list)} table(s)")
                except Exception as e:
                    logger.warning(f"Lines table detection failed: {e}")

            # Strategy 3: Text-inferred columns
            if not tables_list:
                try:
                    tables = page.find_tables(
                        vertical_strategy="text",
                        horizontal_strategy="text",
                        snap_tolerance=15,
                    )
                    tables_list = list(tables) if tables else []
                    logger.info(f"Page {page_num + 1}: Text strategy found {len(tables_list)} table(s)")
                except Exception as e:
                    logger.warning(f"Text table detection failed: {e}")

            if not tables_list:
                logger.warning(f"No tables found on page {page_num + 1} with any strategy")
                continue

            for table_idx, table in enumerate(tables_list):
                logger.info(f"Processing table {table_idx + 1} on page {page_num + 1}")
                rows = table.extract()
                logger.info(f"Table {table_idx + 1}: {len(rows)} rows before merging")

                for idx, row in enumerate(rows[:5]):
                    logger.debug(f"Row {idx}: {row}")

                merged_rows = self._merge_multiline_rows(rows)
                logger.info(f"Table {table_idx + 1}: {len(merged_rows)} rows after merging")

                # SC format: DATE | DESCRIPTION | DEBIT | CREDIT | BALANCE
                for row_idx, row in enumerate(merged_rows):
                    if not row or len(row) < 3:
                        continue

                    first_cell = str(row[0]).strip() if row[0] else ''
                    description = str(row[1]).strip() if len(row) > 1 and row[1] else ''
                    _is_scb_bbf = "balance brought forward" in description.lower()

                    if first_cell.lower() in _scb_skip_keywords:
                        logger.debug(f"Skipping header/summary row: {first_cell}")
                        continue

                    if not first_cell and not _is_scb_bbf:
                        continue

                    if not self._is_date_field(first_cell):
                        if not _is_scb_bbf:
                            logger.debug(f"Skipping non-date row: {first_cell}")
                            continue

                    try:
                        date_str = (
                            self._normalize_date(first_cell)
                            if first_cell and self._is_date_field(first_cell)
                            else ""
                        )
                        if _is_scb_bbf and not date_str:
                            date_str = ""
                        withdrawal_str = str(row[2]).strip() if len(row) > 2 and row[2] else '0'
                        deposit_str = str(row[3]).strip() if len(row) > 3 and row[3] else '0'
                        balance_str = str(row[4]).strip() if len(row) > 4 and row[4] else ''

                        deposit = self._parse_amount(deposit_str)
                        withdrawal = self._parse_amount(withdrawal_str)
                        balance = self._parse_amount(balance_str) if balance_str else None
                        if _is_scb_bbf:
                            deposit = None
                            withdrawal = None
                            amount = 0.0
                        else:
                            amount = deposit if deposit > 0 else -withdrawal
                        reference = self._extract_reference(description)

                        _norm_d = self._normalize_date(date_str) if date_str else ""
                        transaction = {
                            'date': _norm_d,
                            'bank_date': _norm_d,
                            'description': description,
                            'description_raw': description,
                            'deposit': deposit,
                            'withdrawal': withdrawal,
                            'amount': amount,
                            'balance': balance,
                            'reference': reference,
                            'currency': 'HKD',
                            'value_date': None,
                            'transaction_type': self._classify_transaction(description),
                            'account_type': inferred_account_type,
                            '賬戶類型': inferred_account_type,
                        }

                        transactions.append(transaction)
                        logger.debug(f"Parsed SCB transaction: {date_str} - {description} - {amount}")

                    except Exception as e:
                        logger.error(f"Failed to parse SCB row {row_idx}: {row}")
                        logger.error(f"Error: {e}", exc_info=True)
                        continue

        logger.info(f"Successfully parsed {len(transactions)} transactions from SCB statement")
        if len(transactions) < 5:
            logger.warning(f"Only {len(transactions)} transactions found — this might be incomplete")

        if not transactions:
            logger.warning("No transactions extracted from SCB tables, trying VLM fallback")
            transactions = await self._parse_with_ocr_fallback(
                file_path,
                'SCB',
                company_identity=company_identity,
                progress_callback=progress_callback,
                page_verification_out=page_verification_out,
            )

        return transactions

    # ── SCB V2 Pipeline — prescan-driven: PyMuPDF supplies amounts, VLM reads text ──
    # Mirrors HSBC V2 architecture: Stage 1 prescan → Stage 2a PyMuPDF desc → Stage 2b VLM
    # SCB column layout: DATE | DESCRIPTION | DEBIT | CREDIT | BALANCE

    SCB_SECTION_STRINGS = [
        "HKD Current Account",
        "HKD Savings Account",
        "USD Current Account",
        "USD Savings Account",
        "CNY Savings Account",
    ]

    _SCB_V2_CURRENCY = {
        "HKD Current Account": "HKD",
        "HKD Savings Account": "HKD",
        "USD Current Account": "USD",
        "USD Savings Account": "USD",
        "CNY Savings Account": "CNY",
    }

    @staticmethod
    def _scb_v2_bf_opening_by_section(
        sections: List[dict],
        amounts: List[dict],
        balances: List[dict],
        section_for_y: Callable[[float], str],
        date_for_y: Callable[[float], str],
        normalize_date: Callable[[str], str],
        *,
        header_y: float = 0.0,
    ) -> Dict[str, Dict[str, Any]]:
        """Synthesize Balance Brought Forward rows from balance-only lines before first Dr/Cr."""
        bf_by_section: Dict[str, Dict[str, Any]] = {}
        sections_eff: List[dict] = list(sections)
        if not sections_eff and amounts and balances:
            hy = float(header_y or 0.0)
            if hy > 1.0:
                ysec = max(0.0, hy - 8.0)
            else:
                ysec = max(0.0, min(float(a["y"]) for a in amounts) - 120.0)
            sections_eff = [{"y": ysec, "header": "HKD Current Account"}]
        for si, sec in enumerate(sections_eff):
            header = sec["header"]
            if not str(header).strip():
                continue
            section_amounts = [
                a for a in amounts if section_for_y(float(a["y"])) == header
            ]
            if not section_amounts:
                continue
            y_first = min(float(a["y"]) for a in section_amounts)
            baseline = float(header_y or 0.0) + 4.0
            if si == 0:
                y_lo = min(float(sec["y"]), baseline) - 2.0
            else:
                before_headers = {sections[j]["header"] for j in range(si)}
                prev_amt_ys = [
                    float(a["y"])
                    for a in amounts
                    if section_for_y(float(a["y"])) in before_headers
                ]
                y_lo = max(prev_amt_ys) if prev_amt_ys else baseline
                y_lo -= 25.0
            if y_lo < 0.0:
                y_lo = 0.0
            cands = [b for b in balances if y_lo < float(b["y"]) < y_first]
            if not cands and balances:
                y_floor = max(0.0, float(header_y or 0.0) - 45.0)
                cands = [b for b in balances if y_floor < float(b["y"]) < y_first]
            if not cands:
                continue
            b_open = max(cands, key=lambda b: float(b["y"]))
            dt_label = date_for_y(float(b_open["y"]))
            txn_date = normalize_date(dt_label)
            bf_by_section[header] = {
                "transaction_date": txn_date or None,
                "value_date": None,
                "description": "Balance Brought Forward",
                "deposit": None,
                "withdrawal": None,
                "balance": b_open["amount"],
                "currency": BankStatementParser._SCB_V2_CURRENCY.get(header, "HKD"),
                "account_type": header,
                "account_number": None,
                "categorise": "",
                "confidence_score": 1.0,
            }
        return bf_by_section

    @staticmethod
    def _scb_prescan_amounts(page) -> dict:
        """Extract amounts, Dr/Cr column, dates, sections, balances from SCB page text.

        SCB layout:  DATE | DESCRIPTION | DEBIT | CREDIT | BALANCE
        Column headers use "debit"/"credit"/"balance".

        Returns dict with same shape as HSBC prescan for consistent downstream use.
        """
        import re as _re

        AMOUNT_RE = _re.compile(
            r'^\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$'
            r'|^\d{4,}(?:\.\d{1,2})?$'
            r'|^\d+\.\d{2}$'
        )

        DATE_RE = _re.compile(
            r'^\d{1,2}/\d{1,2}/\d{4}$'
            r'|^\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{4}$',
            _re.IGNORECASE,
        )

        words = page.get_text("words")
        page_width = page.rect.width
        page_height = page.rect.height

        empty_result = {
            "amounts": [], "balances": [], "sections": [], "dates": [],
            "header_y": 0.0,
            "debit_hdr_x": page_width * 0.58,
            "credit_hdr_x": page_width * 0.72,
            "bal_hdr_x": page_width * 0.88,
            "desc_right_x": page_width * 0.55,
            "page_height": page_height, "no_table": True,
        }

        if not words:
            return empty_result

        # Log first ~30 words for debugging column-header detection
        _sample = [w[4].strip() for w in words[:30]]
        logger.debug("[SCB-PRESCAN-V2] First 30 words: %s", _sample)

        debit_hdr_x: float | None = None
        credit_hdr_x: float | None = None
        bal_hdr_x: float | None = None
        header_y: float | None = None

        _DEBIT_WORDS = {"debit", "withdrawal", "withdrawals", "dr"}
        _CREDIT_WORDS = {"credit", "deposit", "deposits", "cr"}

        for w in words:
            txt = w[4].strip().lower()
            cx = (w[0] + w[2]) / 2
            if cx < page_width * 0.35:
                continue
            if txt in _DEBIT_WORDS and debit_hdr_x is None:
                debit_hdr_x = cx
                header_y = w[1]
            elif txt in _CREDIT_WORDS and credit_hdr_x is None:
                credit_hdr_x = cx
                if header_y is None:
                    header_y = w[1]
            elif txt == "balance" and bal_hdr_x is None and cx > page_width * 0.5:
                bal_hdr_x = cx

        if debit_hdr_x is None and credit_hdr_x is None:
            logger.info(
                "[SCB-PRESCAN-V2] No debit/credit headers found. "
                "All words in right half: %s",
                [w[4].strip() for w in words if (w[0]+w[2])/2 > page_width*0.35][:20],
            )
            return empty_result

        if debit_hdr_x is None:
            debit_hdr_x = page_width * 0.58
        if credit_hdr_x is None:
            credit_hdr_x = page_width * 0.72
        if bal_hdr_x is None:
            bal_hdr_x = page_width * 0.88

        min_y = (header_y or 0) + 4
        desc_right_x = debit_hdr_x - 35.0

        dr_lo, dr_hi = debit_hdr_x - 35.0, debit_hdr_x + 35.0
        cr_lo, cr_hi = credit_hdr_x - 35.0, credit_hdr_x + 35.0
        bal_lo, bal_hi = bal_hdr_x - 40.0, bal_hdr_x + 40.0

        amounts: list[dict] = []
        balances: list[dict] = []

        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
            if y0 < min_y:
                continue
            if not AMOUNT_RE.match(text):
                continue
            x_mid = (x0 + x1) / 2
            if x_mid < page_width * 0.20:
                continue
            amt_val = float(text.replace(",", ""))
            if text.isdigit() and 1900 <= amt_val <= 2100:
                continue
            if dr_lo <= x_mid <= dr_hi:
                amounts.append({"y": y0, "col": "Dr", "amount": amt_val, "text": text})
            elif cr_lo <= x_mid <= cr_hi:
                amounts.append({"y": y0, "col": "Cr", "amount": amt_val, "text": text})
            elif bal_lo <= x_mid <= bal_hi:
                balances.append({"y": y0, "amount": amt_val})

        amounts.sort(key=lambda r: r["y"])
        balances.sort(key=lambda r: r["y"])

        sections: list[dict] = []
        from collections import defaultdict as _dd
        line_words: dict[int, list] = _dd(list)
        for w in words:
            bucket = int(round(w[1] / 3.0)) * 3
            line_words[bucket].append(w)

        for bucket in sorted(line_words):
            ws = sorted(line_words[bucket], key=lambda w_: w_[0])
            line_text = " ".join(w_[4].strip() for w_ in ws)
            line_y = ws[0][1]
            for sec in BankStatementParser.SCB_SECTION_STRINGS:
                if sec.lower() in line_text.lower():
                    sections.append({"y": line_y, "header": sec})
                    break

        sections.sort(key=lambda r: r["y"])

        dates: list[dict] = []
        for w in words:
            x0, y0 = w[0], w[1]
            text = w[4].strip()
            if y0 < min_y:
                continue
            if x0 > page_width * 0.18:
                continue
            if DATE_RE.match(text):
                dates.append({"y": y0, "text": text})

        dates.sort(key=lambda r: r["y"])

        logger.debug(
            "[SCB-PRESCAN-V2] amounts=%d (Cr=%d Dr=%d) balances=%d "
            "sections=%d dates=%d",
            len(amounts),
            sum(1 for a in amounts if a["col"] == "Cr"),
            sum(1 for a in amounts if a["col"] == "Dr"),
            len(balances), len(sections), len(dates),
        )
        return {
            "amounts": amounts, "balances": balances,
            "sections": sections, "dates": dates,
            "header_y": header_y or 0.0,
            "debit_hdr_x": debit_hdr_x, "credit_hdr_x": credit_hdr_x,
            "bal_hdr_x": bal_hdr_x,
            "desc_right_x": desc_right_x,
            "page_height": page_height, "no_table": False,
        }

    @staticmethod
    def _scb_extract_descriptions(page, amounts: list, ps: dict) -> list:
        """Extract descriptions for each prescan amount via PyMuPDF text layer.

        Description column x-range: 12% of page width → debit_hdr_x − 35pt.
        Y-band: exclusive per-transaction, same logic as HSBC V2.
        """
        if not amounts:
            return []

        words = page.get_text("words")
        page_width = page.rect.width
        desc_right_x = ps["desc_right_x"]

        desc_x_left = page_width * 0.12
        desc_x_right = desc_right_x

        y_bands: list[tuple[float, float]] = []
        for i, amt in enumerate(amounts):
            y_top = amt["y"] - 2.0
            if i + 1 < len(amounts):
                y_bot = min(amt["y"] + 22.0, amounts[i + 1]["y"] - 1.0)
            else:
                y_bot = amt["y"] + 22.0
            y_bands.append((y_top, y_bot))

        desc_words: list[list[tuple]] = [[] for _ in amounts]

        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
            if not text:
                continue
            x_mid = (x0 + x1) / 2
            if not (desc_x_left <= x_mid <= desc_x_right):
                continue
            for i, (y_top, y_bot) in enumerate(y_bands):
                if y_top <= y0 <= y_bot:
                    desc_words[i].append((y0, x0, text))
                    break

        descriptions: list[str] = []
        for word_list in desc_words:
            word_list.sort(key=lambda t: (t[0], t[1]))
            descriptions.append(" ".join(t[2] for t in word_list))

        return descriptions

    async def _scb_process_page_v2(
        self,
        page,
        page_num: int,
        page_count: int,
        vlm_model: str,
        company_identity: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """Prescan-driven SCB pipeline (V2).

        Stage 1 — Prescan    : PyMuPDF extracts amounts, Dr/Cr, dates, sections.
        Stage 2a — PyMuPDF   : Extract descriptions from text layer.
        Stage 2b — VLM       : Fallback for scanned/image PDFs (descriptions only).
        Stage 3 — Merge      : Combine prescan amounts with descriptions.
        """
        import re as _re
        from app.ocr.runtime import ocr_service as _ocr_service

        _page_text = (page.get_text() or "").lower()
        if "presented cheques" in _page_text or "by cheque no" in _page_text:
            logger.info("[SCB-V2][P%d] Presented Cheques page — skipping (not transactions)", page_num + 1)
            return []

        ps = BankStatementParser._scb_prescan_amounts(page)

        if ps["no_table"]:
            logger.info("[SCB-V2][P%d] No transaction table detected — skipping", page_num + 1)
            return []

        amounts = ps["amounts"]
        balances = ps["balances"]
        sections = ps["sections"]
        dates = ps["dates"]
        page_height = ps["page_height"]

        logger.info(
            "[SCB-V2][P%d] Prescan: %d amounts (Cr=%d, Dr=%d), %d sections, %d dates",
            page_num + 1, len(amounts),
            sum(1 for a in amounts if a["col"] == "Cr"),
            sum(1 for a in amounts if a["col"] == "Dr"),
            len(sections), len(dates),
        )

        if not amounts and not sections:
            logger.warning("[SCB-V2][P%d] No amounts or sections — skipping", page_num + 1)
            return []

        pymu_descs: list[str] | None = None

        if amounts:
            raw_descs = BankStatementParser._scb_extract_descriptions(page, amounts, ps)
            filled = sum(1 for d in raw_descs if d.strip())
            fill_rate = filled / len(amounts) if amounts else 0
            logger.info(
                "[SCB-V2][P%d] PyMuPDF descriptions: %d/%d rows have text (%.0f%%)",
                page_num + 1, filled, len(amounts), fill_rate * 100,
            )
            if fill_rate >= 0.50:
                pymu_descs = raw_descs
                logger.info("[SCB-V2][P%d] Using PyMuPDF descriptions (digital PDF)", page_num + 1)

        vlm_rows: list[dict] = []

        if pymu_descs is None:
            logger.info("[SCB-V2][P%d] PyMuPDF text sparse — falling back to VLM", page_num + 1)
            import fitz
            import numpy as _np
            import cv2 as _cv2
            import tempfile as _tempfile
            import json as _json
            from app.bank_prompts.sc import PROMPT_V2 as _SCB_PROMPT_V2

            render_dpi = int(os.getenv("SCB_V2_RENDER_DPI", "300"))
            render_scale = render_dpi / 72.0
            pix = page.get_pixmap(
                matrix=fitz.Matrix(render_scale, render_scale),
                colorspace=fitz.csRGB,
            )
            img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
                pix.height, pix.width, 3
            )
            img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)

            jpeg_q = int(os.getenv("SCB_V2_JPEG_QUALITY", "90"))
            max_side = int(os.getenv("SCB_V2_MAX_SIDE", "2000"))
            image_opts = {"max_side": max_side, "format": "JPEG", "quality": jpeg_q}

            tmp_path = ""
            try:
                tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tmp_path = tmp.name
                tmp.close()
                _cv2.imwrite(tmp_path, img_bgr, [_cv2.IMWRITE_JPEG_QUALITY, jpeg_q])

                page_hash = hashlib.sha256(pix.samples).hexdigest()
                vlm_txns = await self._run_vlm_track(
                    tmp_path, _SCB_PROMPT_V2, page_hash,
                    vlm_model, "SCB-V2-DESC", company_identity,
                    max_tokens=8000, image_options=image_opts,
                )
                raw_text = ""
                if vlm_txns:
                    raw_text = str(vlm_txns)
                from app.ocr.runtime import ocr_service as _svc
                prompt_hash = hashlib.sha256(_SCB_PROMPT_V2.encode()).hexdigest()[:16]
                cache_key = f"bank-vlm:nothink:{page_hash}:{vlm_model}:{prompt_hash}:8000"
                cached = self._get_cached_ocr_text(cache_key) or ""
                if cached:
                    raw_clean = cached.strip()
                    raw_clean = _re.sub(r'^```[a-z]*\n?', '', raw_clean)
                    raw_clean = _re.sub(r'\n?```$', '', raw_clean).strip()
                    try:
                        parsed = _json.loads(raw_clean)
                        vlm_rows = parsed.get("rows", [])
                        logger.info("[SCB-V2][P%d] VLM JSON parsed: %d rows", page_num + 1, len(vlm_rows))
                    except _json.JSONDecodeError as je:
                        logger.warning("[SCB-V2][P%d] VLM JSON failed (%s); raw=%.300s", page_num + 1, je, raw_clean)
                        vlm_rows = []
            except Exception as vlm_err:
                logger.error("[SCB-V2][P%d] VLM call failed: %s", page_num + 1, vlm_err, exc_info=True)
                vlm_rows = []
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except PermissionError:
                        pass

        def _vlm_y_to_pts(y_pct: float) -> float:
            return (float(y_pct) / 100.0) * page_height

        vlm_pts = [_vlm_y_to_pts(r.get("y_pct", 50)) for r in vlm_rows]
        _MAX_MATCH_DIST = page_height * 0.08

        def _closest_vlm(y_pdf: float) -> dict | None:
            if not vlm_rows:
                return None
            best_idx = min(range(len(vlm_rows)), key=lambda i: abs(vlm_pts[i] - y_pdf))
            if abs(vlm_pts[best_idx] - y_pdf) > _MAX_MATCH_DIST:
                return None
            return vlm_rows[best_idx]

        def _section_for_y(y_pdf: float) -> str:
            chosen = "HKD Current Account"
            for s in sections:
                if s["y"] <= y_pdf:
                    chosen = s["header"]
                else:
                    break
            return chosen

        def _date_for_y(y_pdf: float) -> str:
            chosen = ""
            for d in dates:
                if d["y"] <= y_pdf + 3.0:
                    chosen = d["text"]
                else:
                    break
            return chosen

        def _balance_for_y(y_pdf: float) -> float | None:
            for b in balances:
                if b["y"] >= y_pdf - 5.0:
                    return b["amount"]
            return None

        def _normalize_scb_date(raw: str) -> str:
            if not raw:
                return ""
            import re as _r
            m1 = _r.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', raw)
            if m1:
                dd, mm, yyyy = m1.group(1), m1.group(2), m1.group(3)
                return f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"
            MONTH_MAP = {
                "jan": "01", "feb": "02", "mar": "03", "apr": "04",
                "may": "05", "jun": "06", "jul": "07", "aug": "08",
                "sep": "09", "oct": "10", "nov": "11", "dec": "12",
            }
            m2 = _r.match(r'^(\d{1,2})\s+(\w{3})\s+(\d{4})$', raw, _r.IGNORECASE)
            if m2:
                dd = m2.group(1).zfill(2)
                mm = MONTH_MAP.get(m2.group(2).lower()[:3], "01")
                yyyy = m2.group(3)
                return f"{yyyy}-{mm}-{dd}"
            m3 = _r.match(r'^([A-Za-z]{3,9})\s+(\d{4})$', raw.strip(), _r.IGNORECASE)
            if m3:
                mm = MONTH_MAP.get(m3.group(1).lower()[:3], "01")
                yyyy = m3.group(2)
                return f"{yyyy}-{mm}-01"
            return raw

        def _scb_date_from_description_prefix(desc: str) -> str:
            import re as _r
            s = (desc or "").strip()
            m = _r.match(
                r'^((?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)'
                r'[a-z]*\s+\d{4})\b',
                s,
                _r.IGNORECASE,
            )
            return m.group(1).strip() if m else ""

        _SECTION_CURRENCY = {
            "HKD Current Account": "HKD",
            "HKD Savings Account": "HKD",
            "USD Current Account": "USD",
            "USD Savings Account": "USD",
            "CNY Savings Account": "CNY",
        }

        bf_by_section = BankStatementParser._scb_v2_bf_opening_by_section(
            sections,
            amounts,
            balances,
            _section_for_y,
            _date_for_y,
            _normalize_scb_date,
            header_y=float(ps.get("header_y") or 0.0),
        )
        if bf_by_section:
            logger.info(
                "[SCB-V2][P%d] Balance Brought Forward rows for sections: %s",
                page_num + 1,
                ", ".join(sorted(bf_by_section.keys())),
            )

        if bf_by_section and amounts:
            _lead_raw = ""
            if pymu_descs is not None and pymu_descs:
                _lead_raw = _scb_date_from_description_prefix(str(pymu_descs[0] or "").strip())
            elif vlm_rows:
                _c0 = _closest_vlm(float(amounts[0]["y"]))
                _lead_raw = _scb_date_from_description_prefix(
                    str(_c0.get("description", "")) if _c0 else ""
                )
            if _lead_raw:
                _iso_bf = _normalize_scb_date(_lead_raw)
                if _iso_bf and _iso_bf[0:4].isdigit():
                    for _bfv in bf_by_section.values():
                        if not str(_bfv.get("transaction_date") or "").strip():
                            _bfv["transaction_date"] = _iso_bf

        out_txns: List[Dict[str, Any]] = []
        emitted_bf_for_section: set[str] = set()

        for i, amt_rec in enumerate(amounts):
            y_pdf = amt_rec["y"]
            acct_eff = _section_for_y(y_pdf)
            if acct_eff not in emitted_bf_for_section:
                bf_row = bf_by_section.get(acct_eff)
                if bf_row is not None:
                    out_txns.append(dict(bf_row))
                emitted_bf_for_section.add(acct_eff)

            col = amt_rec["col"]
            amount = amt_rec["amount"]

            if pymu_descs is not None:
                desc = pymu_descs[i].strip()
                dt_raw = _date_for_y(y_pdf)
                acct_type = _section_for_y(y_pdf)
            else:
                closest = _closest_vlm(y_pdf)
                desc = closest.get("description", "").strip() if closest else ""
                acct_type = closest.get("account_type", "").strip() if closest else ""
                dt_raw = closest.get("date_label", "").strip() if closest else ""
                if not dt_raw:
                    dt_raw = _date_for_y(y_pdf)
                if not acct_type or acct_type not in _SECTION_CURRENCY:
                    acct_type = _section_for_y(y_pdf)

            txn_date = _normalize_scb_date(dt_raw)
            if not txn_date and (desc or "").strip():
                txn_date = _normalize_scb_date(_scb_date_from_description_prefix(desc))
            balance = _balance_for_y(y_pdf)
            currency = _SECTION_CURRENCY.get(acct_type, "HKD")

            txn: Dict[str, Any] = {
                "transaction_date": txn_date or None,
                "value_date": None,
                "description": desc or "",
                "deposit": amount if col == "Cr" else None,
                "withdrawal": amount if col == "Dr" else None,
                "balance": balance,
                "currency": currency,
                "account_type": acct_type,
                "account_number": None,
                "categorise": "",
                "confidence_score": 0.90,
            }
            out_txns.append(txn)

        sections_with_amounts: set[str] = set()
        for amt_rec in amounts:
            sections_with_amounts.add(_section_for_y(amt_rec["y"]))

        for sec in sections:
            if sec["header"] not in sections_with_amounts:
                sec_balance = _balance_for_y(sec["y"])
                empty_txn: Dict[str, Any] = {
                    "transaction_date": None,
                    "value_date": None,
                    "description": "無交易",
                    "deposit": None,
                    "withdrawal": None,
                    "balance": sec_balance,
                    "currency": _SECTION_CURRENCY.get(sec["header"], "HKD"),
                    "account_type": sec["header"],
                    "account_number": None,
                    "categorise": "",
                    "confidence_score": 1.0,
                }
                out_txns.append(empty_txn)

        logger.info(
            "[SCB-V2][P%d] Merged %d transactions (%d empty-section rows)",
            page_num + 1, len(out_txns),
            sum(1 for t in out_txns if t["description"] == "無交易"),
        )
        return out_txns

    # ── BOCOM V2 Pipeline — prescan-driven: PyMuPDF supplies amounts, VLM reads text ─
    # Mirrors HSBC/SCB V2 architecture: Stage 1 prescan → Stage 2a PyMuPDF desc → Stage 2b VLM
    # BOCOM column layout: DATE | DESCRIPTION | CURRENCY | WITHDRAWALS | DEPOSITS | BALANCE

    BOCOM_SECTION_STRINGS = [
        "儲蓄存款 SAVINGS",
        "支票活期存款 CURRENT",
    ]

    _BOCOM_SKIP_PATTERNS = {
        "承前餘額", "承前", "BAL B/F", "BAL.B/F",
        "交易總金額", "TOTAL TRANSACTION AMOUNT",
        "交易筆數", "NO.OF TRANSACTION",
        "TO BE CONTINUED", "END OF STATEMENT",
        "存款總數", "TOTAL DEPOSITS",
    }

    @staticmethod
    def _bocom_prescan_amounts(page) -> dict:
        """Extract amounts, Dr/Cr column, dates, sections, balances from BOCOM page text.

        BOCOM layout: DATE | DESCRIPTION | CURRENCY | WITHDRAWALS | DEPOSITS | BALANCE
        Column headers contain '支出'/'WITHDRAWALS' and '存入'/'DEPOSITS'.

        Returns dict with same shape as HSBC/SCB prescan for consistent downstream use.
        """
        import re as _re

        AMOUNT_RE = _re.compile(
            r'^\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$'
            r'|^\d{4,}(?:\.\d{1,2})?$'
            r'|^\d+\.\d{2}$'
        )

        BOCOM_DATE_RE = _re.compile(r'^\d{4}/\d{2}/\d{2}$')

        words = page.get_text("words")   # (x0, y0, x1, y1, text, blk, ln, wi)
        page_width  = page.rect.width
        page_height = page.rect.height

        empty_result = {
            "amounts": [], "balances": [], "sections": [], "dates": [],
            "header_y": 0.0,
            "wdw_hdr_x": page_width * 0.55,
            "dep_hdr_x": page_width * 0.70,
            "bal_hdr_x": page_width * 0.88,
            "desc_right_x": page_width * 0.48,
            "page_height": page_height, "no_table": True,
        }

        if not words:
            return empty_result

        _sample = [w[4].strip() for w in words[:30]]
        logger.debug("[BOCOM-PRESCAN-V2] First 30 words: %s", _sample)

        # ── Locate column header x-positions (two-pass) ────────────────────
        # "WITHDRAWALS" / "支出" are unique to the transaction-table column header.
        # "DEPOSITS" also appears in the Page-1 section title
        #   "SAVINGS/CURRENT DEPOSITS ACTIVITIES"
        # at a completely wrong x-position.  A single-pass scan would pick up
        # that title word first and poison dep_hdr_x.
        #
        # Strategy: Pass 1 — anchor on "WITHDRAWALS"/"支出" (safe keyword).
        #           Pass 2 — find "DEPOSITS"/"存入" and "BALANCE"/"結餘"
        #                    only on the SAME y-line (±12 pt) as the anchor.
        wdw_hdr_x: float | None = None
        dep_hdr_x: float | None = None
        bal_hdr_x: float | None = None
        header_y:  float | None = None

        _WDW_WORDS = {"withdrawals", "withdrawal"}
        _DEP_WORDS = {"deposits", "deposit"}

        # BOCOM has 6 columns — amount columns start at ~52% width.
        _LEFT_FILTER = page_width * 0.42

        # Pass 1: find the anchor keyword "WITHDRAWALS" / "支出"
        for w in words:
            txt = w[4].strip().lower()
            cx  = (w[0] + w[2]) / 2
            if cx < _LEFT_FILTER:
                continue
            if txt in _WDW_WORDS or "支出" in txt:
                wdw_hdr_x = cx
                header_y  = w[1]
                break   # first match is the column header (top-to-bottom)

        # Pass 2: find "DEPOSITS"/"存入" and "BALANCE"/"結餘" near header_y
        _Y_TOL = 12.0   # Chinese line above English line ≈ 10-12 pt apart
        for w in words:
            txt = w[4].strip().lower()
            cx  = (w[0] + w[2]) / 2
            if cx < _LEFT_FILTER:
                continue
            # If we have an anchor, restrict to the same y-band
            if header_y is not None and abs(w[1] - header_y) > _Y_TOL:
                continue
            if (txt in _DEP_WORDS or "存入" in txt) and dep_hdr_x is None:
                dep_hdr_x = cx
                if header_y is None:
                    header_y = w[1]
            elif (txt == "balance" or "結餘" in txt) and bal_hdr_x is None and cx > page_width * 0.5:
                bal_hdr_x = cx

        if wdw_hdr_x is None and dep_hdr_x is None:
            logger.info(
                "[BOCOM-PRESCAN-V2] No withdrawal/deposit headers found. "
                "All words in right half: %s",
                [w[4].strip() for w in words if (w[0]+w[2])/2 > _LEFT_FILTER][:30],
            )
            return empty_result  # no_table=True

        if wdw_hdr_x is None:
            wdw_hdr_x = page_width * 0.55
        if dep_hdr_x is None:
            dep_hdr_x = page_width * 0.70
        if bal_hdr_x is None:
            bal_hdr_x = page_width * 0.88

        # BOCOM column headers span 3 lines (Chinese + English + "ORIGINAL CURRENCY").
        # +20pt clears all header lines before the first transaction row.
        min_y = (header_y or 0) + 20
        # Description right boundary: stop before the currency column (~42% page width).
        desc_right_x = min(wdw_hdr_x - 50.0, page_width * 0.42)

        wdw_lo, wdw_hi = wdw_hdr_x - 35.0, wdw_hdr_x + 35.0
        dep_lo, dep_hi = dep_hdr_x - 35.0, dep_hdr_x + 35.0
        bal_lo, bal_hi = bal_hdr_x - 40.0, bal_hdr_x + 40.0

        amounts:  list[dict] = []
        balances: list[dict] = []

        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
            if y0 < min_y:
                continue
            if not AMOUNT_RE.match(text):
                continue
            x_mid = (x0 + x1) / 2
            if x_mid < _LEFT_FILTER:
                continue
            amt_val = float(text.replace(",", ""))
            if text.isdigit() and 1900 <= amt_val <= 2100:
                continue
            # BOCOM amounts always have decimal places (e.g. 1,918.10).
            # Bare integers > 999 are reference/account numbers (e.g. 54991270)
            # embedded in the description area that happen to match AMOUNT_RE.
            if "." not in text and amt_val > 999:
                continue
            if wdw_lo <= x_mid <= wdw_hi:
                amounts.append({"y": y0, "col": "Dr", "amount": amt_val, "text": text})
            elif dep_lo <= x_mid <= dep_hi:
                amounts.append({"y": y0, "col": "Cr", "amount": amt_val, "text": text})
            elif bal_lo <= x_mid <= bal_hi:
                balances.append({"y": y0, "amount": amt_val})

        amounts.sort(key=lambda r: r["y"])
        balances.sort(key=lambda r: r["y"])

        # ── Detect account section headers ───────────────────────────────────
        # Only consider lines BELOW the column header area (min_y - 10 to allow
        # section headers that appear just before the first transaction).
        # This prevents the Page 1 account summary table from generating false
        # section headers (e.g. "儲蓄存款 SAVINGS HKD 000000000000001" in summary).
        _section_min_y = min_y - 10 if header_y else 0
        sections: list[dict] = []
        from collections import defaultdict as _dd
        line_words: dict[int, list] = _dd(list)
        for w in words:
            if w[1] < _section_min_y:
                continue
            bucket = int(round(w[1] / 3.0)) * 3
            line_words[bucket].append(w)

        # BOCOM section headers contain "：" (full-width colon) followed by account number.
        # Match lines that contain the section keyword AND a colon separator.
        for bucket in sorted(line_words):
            ws = sorted(line_words[bucket], key=lambda w_: w_[0])
            line_text = " ".join(w_[4].strip() for w_ in ws)
            line_y    = ws[0][1]
            line_lower = line_text.lower()
            for sec in BankStatementParser.BOCOM_SECTION_STRINGS:
                kw_cn = sec.split()[0]   # "儲蓄存款" or "支票活期存款"
                kw_en = sec.split()[-1]  # "SAVINGS" or "CURRENT"
                if (kw_cn in line_text or kw_en.lower() in line_lower):
                    # Require "：" or ":" to distinguish section headers from
                    # summary mentions (summary has no colon after the label)
                    if "：" in line_text or ":" in line_text or "BAL B/F" in line_text.upper():
                        acct_num = ""
                        import re as _re2
                        m = _re2.search(r'\d{10,}', line_text)
                        if m:
                            acct_num = m.group()
                        sections.append({"y": line_y, "header": sec, "account_number": acct_num})
                        break

        sections.sort(key=lambda r: r["y"])

        # ── Detect dates (YYYY/MM/DD in left portion) ────────────────────────
        dates: list[dict] = []
        for w in words:
            x0, y0 = w[0], w[1]
            text = w[4].strip()
            if y0 < min_y:
                continue
            if x0 > page_width * 0.18:
                continue
            if BOCOM_DATE_RE.match(text):
                dates.append({"y": y0, "text": text})

        dates.sort(key=lambda r: r["y"])

        logger.info(
            "[BOCOM-PRESCAN-V2] amounts=%d (Cr=%d Dr=%d) balances=%d "
            "sections=%d dates=%d | hdr_y=%.1f wdw_x=%.1f dep_x=%.1f bal_x=%.1f "
            "min_y=%.1f desc_right_x=%.1f",
            len(amounts),
            sum(1 for a in amounts if a["col"] == "Cr"),
            sum(1 for a in amounts if a["col"] == "Dr"),
            len(balances), len(sections), len(dates),
            header_y or 0, wdw_hdr_x, dep_hdr_x, bal_hdr_x,
            min_y, desc_right_x,
        )
        return {
            "amounts": amounts, "balances": balances,
            "sections": sections, "dates": dates,
            "header_y": header_y or 0.0,
            "wdw_hdr_x": wdw_hdr_x, "dep_hdr_x": dep_hdr_x,
            "bal_hdr_x": bal_hdr_x,
            "desc_right_x": desc_right_x,
            "page_height": page_height, "no_table": False,
        }

    @staticmethod
    def _bocom_extract_descriptions(page, amounts: list, ps: dict) -> list:
        """Extract descriptions for each prescan amount via PyMuPDF text layer.

        BOCOM description column: 12% page width → desc_right_x (capped at ~42%).
        Excludes the Currency column (Column 3) to avoid stray "HKD" in descriptions.
        Y-band: 28pt for BOCOM's 2-line descriptions (date+type line + payee line).
        """
        if not amounts:
            return []

        words = page.get_text("words")
        page_width = page.rect.width
        desc_right_x = ps["desc_right_x"]

        desc_x_left  = page_width * 0.12   # after Date column
        desc_x_right = desc_right_x         # before Currency column (~42%)

        # BOCOM transactions typically span 2 printed lines (~28pt at 11pt font).
        _MAX_Y_SPAN = 28.0

        y_bands: list[tuple[float, float]] = []
        for i, amt in enumerate(amounts):
            y_top = amt["y"] - 4.0
            if i + 1 < len(amounts):
                y_bot = min(amt["y"] + _MAX_Y_SPAN, amounts[i + 1]["y"] - 1.0)
            else:
                y_bot = amt["y"] + _MAX_Y_SPAN
            y_bands.append((y_top, y_bot))

        desc_words: list[list[tuple]] = [[] for _ in amounts]

        for w in words:
            x0, y0, x1, y1, text = w[0], w[1], w[2], w[3], w[4].strip()
            if not text:
                continue
            x_mid = (x0 + x1) / 2
            if not (desc_x_left <= x_mid <= desc_x_right):
                continue
            for i, (y_top, y_bot) in enumerate(y_bands):
                if y_top <= y0 <= y_bot:
                    desc_words[i].append((y0, x0, text))
                    break

        descriptions: list[str] = []
        for word_list in desc_words:
            word_list.sort(key=lambda t: (t[0], t[1]))
            descriptions.append(" ".join(t[2] for t in word_list))

        return descriptions

    @staticmethod
    def _bocom_v2_bf_opening_by_section(
        sections: List[dict],
        amounts: List[dict],
        balances: List[dict],
        section_for_y: Callable[[float], str],
        date_for_y: Callable[[float], str],
        normalize_date: Callable[[str], str],
        *,
        header_y: float = 0.0,
    ) -> Dict[str, Dict[str, Any]]:
        """Build one B/F row per BOCOM section that has Cr/Dr prescan amounts.

        Opening lines appear as balance-only rows (no withdrawal/deposit in amount columns).
        Prescan records them in ``balances`` but not in ``amounts`` — same idea as HSBC V2.
        """
        _VALID_ACCT = set(BankStatementParser.BOCOM_SECTION_STRINGS)
        bf_by_section: Dict[str, Dict[str, Any]] = {}

        def _acct_num(header: str) -> str | None:
            for s in sections:
                if s.get("header") == header:
                    n = str(s.get("account_number") or "").strip()
                    return n if n else None
            return None

        for si, sec in enumerate(sections):
            header = sec["header"]
            if header not in _VALID_ACCT:
                continue
            y_sec = float(sec["y"])
            section_amounts = [
                a for a in amounts if section_for_y(float(a["y"])) == header
            ]
            if not section_amounts:
                continue
            y_first = min(float(a["y"]) for a in section_amounts)
            baseline = float(header_y or 0.0) + 4.0
            if si == 0:
                y_lo = min(y_sec, baseline) - 2.0
            else:
                before_headers = {sections[j]["header"] for j in range(si)}
                prev_amt_ys = [
                    float(a["y"])
                    for a in amounts
                    if section_for_y(float(a["y"])) in before_headers
                ]
                y_lo = max(prev_amt_ys) if prev_amt_ys else baseline
                y_lo -= 25.0
            if y_lo < 0.0:
                y_lo = 0.0
            cands = [b for b in balances if y_lo < float(b["y"]) < y_first]
            if not cands and balances:
                y_floor = max(0.0, float(header_y or 0.0) - 45.0)
                cands = [b for b in balances if y_floor < float(b["y"]) < y_first]
            if not cands:
                continue
            b_open = max(cands, key=lambda b: float(b["y"]))
            dt_label = date_for_y(float(b_open["y"]))
            txn_date = normalize_date(dt_label)
            bf_by_section[header] = {
                "transaction_date": txn_date or None,
                "value_date": None,
                "description": "承前餘額 BAL B/F",
                "deposit": None,
                "withdrawal": None,
                "balance": b_open["amount"],
                "currency": "HKD",
                "account_type": header,
                "account_number": _acct_num(header),
                "categorise": "",
                "confidence_score": 1.0,
            }
        return bf_by_section

    async def _bocom_process_page_v2(
        self,
        page,
        page_num: int,
        page_count: int,
        vlm_model: str,
        company_identity: Dict[str, Any] | None = None,
        page_verification_out: Dict[int, str] | None = None,
    ) -> List[Dict[str, Any]]:
        """Prescan-driven BOCOM pipeline (V2).

        Stage 1 — Prescan    : PyMuPDF extracts amounts, Dr/Cr, dates, sections.
        Stage 2a — PyMuPDF   : Extract descriptions from text layer.
        Stage 2b — VLM       : Fallback for scanned/image PDFs (descriptions only).
        Stage 3 — Merge      : Combine prescan amounts with descriptions.
        Optional — AR manager (BANK_CROSS_VLM_*): balance-only merge like HSBC.
        """
        import re as _re
        from app.ocr.runtime import ocr_service as _ocr_service

        ps = BankStatementParser._bocom_prescan_amounts(page)

        if ps["no_table"]:
            logger.info("[BOCOM-V2][P%d] No transaction table detected — skipping", page_num + 1)
            return []

        amounts    = ps["amounts"]
        balances   = ps["balances"]
        sections   = ps["sections"]
        dates      = ps["dates"]
        page_height = ps["page_height"]

        logger.info(
            "[BOCOM-V2][P%d] Prescan: %d amounts (Cr=%d, Dr=%d), %d sections, %d dates",
            page_num + 1, len(amounts),
            sum(1 for a in amounts if a["col"] == "Cr"),
            sum(1 for a in amounts if a["col"] == "Dr"),
            len(sections), len(dates),
        )

        if not amounts and not sections:
            logger.warning("[BOCOM-V2][P%d] No amounts or sections — skipping", page_num + 1)
            return []

        # ── Stage 2a: PyMuPDF description extraction ─────────────────────────
        pymu_descs: list[str] | None = None

        if amounts:
            raw_descs = BankStatementParser._bocom_extract_descriptions(page, amounts, ps)
            filled    = sum(1 for d in raw_descs if d.strip())
            fill_rate = filled / len(amounts) if amounts else 0
            logger.info(
                "[BOCOM-V2][P%d] PyMuPDF descriptions: %d/%d rows have text (%.0f%%)",
                page_num + 1, filled, len(amounts), fill_rate * 100,
            )
            if fill_rate >= 0.50:
                pymu_descs = raw_descs
                logger.info("[BOCOM-V2][P%d] Using PyMuPDF descriptions (digital PDF)", page_num + 1)

        # ── Stage 2b: VLM fallback (scanned / image-only pages) ──────────────
        vlm_rows: list[dict] = []

        if pymu_descs is None:
            logger.info("[BOCOM-V2][P%d] PyMuPDF text sparse — falling back to VLM", page_num + 1)
            import fitz
            import numpy as _np
            import cv2 as _cv2
            import tempfile as _tempfile
            import json as _json
            from app.bank_prompts.bocom import PROMPT_V2 as _BOCOM_PROMPT_V2

            render_dpi   = int(os.getenv("BOCOM_V2_RENDER_DPI", "300"))
            render_scale = render_dpi / 72.0
            pix = page.get_pixmap(
                matrix=fitz.Matrix(render_scale, render_scale),
                colorspace=fitz.csRGB,
            )
            img_rgb = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(
                pix.height, pix.width, 3
            )
            img_bgr = _cv2.cvtColor(img_rgb, _cv2.COLOR_RGB2BGR)

            jpeg_q   = int(os.getenv("BOCOM_V2_JPEG_QUALITY", "90"))
            max_side = int(os.getenv("BOCOM_V2_MAX_SIDE", "2000"))
            image_opts = {"max_side": max_side, "format": "JPEG", "quality": jpeg_q}

            tmp_path = ""
            try:
                tmp = _tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
                tmp_path = tmp.name
                tmp.close()
                _cv2.imwrite(tmp_path, img_bgr, [_cv2.IMWRITE_JPEG_QUALITY, jpeg_q])

                page_hash = hashlib.sha256(pix.samples).hexdigest()
                vlm_txns = await self._run_vlm_track(
                    tmp_path, _BOCOM_PROMPT_V2, page_hash,
                    vlm_model, "BOCOM-V2-DESC", company_identity,
                    max_tokens=8000, image_options=image_opts,
                )
                prompt_hash = hashlib.sha256(_BOCOM_PROMPT_V2.encode()).hexdigest()[:16]
                cache_key = f"bank-vlm:nothink:{page_hash}:{vlm_model}:{prompt_hash}:8000"
                cached = self._get_cached_ocr_text(cache_key) or ""
                if cached:
                    raw_clean = cached.strip()
                    raw_clean = _re.sub(r'^```[a-z]*\n?', '', raw_clean)
                    raw_clean = _re.sub(r'\n?```$', '', raw_clean).strip()
                    try:
                        parsed   = _json.loads(raw_clean)
                        vlm_rows = parsed.get("rows", [])
                        logger.info("[BOCOM-V2][P%d] VLM JSON parsed: %d rows", page_num + 1, len(vlm_rows))
                    except _json.JSONDecodeError as je:
                        logger.warning("[BOCOM-V2][P%d] VLM JSON failed (%s); raw=%.300s", page_num + 1, je, raw_clean)
                        vlm_rows = []
            except Exception as vlm_err:
                logger.error("[BOCOM-V2][P%d] VLM call failed: %s", page_num + 1, vlm_err, exc_info=True)
                vlm_rows = []
            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except PermissionError:
                        pass

        # ── Stage 3: Merge ───────────────────────────────────────────────────
        def _vlm_y_to_pts(y_pct: float) -> float:
            return (float(y_pct) / 100.0) * page_height

        vlm_pts = [_vlm_y_to_pts(r.get("y_pct", 50)) for r in vlm_rows]
        _MAX_MATCH_DIST = page_height * 0.08

        def _closest_vlm(y_pdf: float) -> dict | None:
            if not vlm_rows:
                return None
            best_idx = min(range(len(vlm_rows)), key=lambda i: abs(vlm_pts[i] - y_pdf))
            if abs(vlm_pts[best_idx] - y_pdf) > _MAX_MATCH_DIST:
                return None
            return vlm_rows[best_idx]

        def _section_for_y(y_pdf: float) -> tuple[str, str]:
            """Return (section_header, account_number) in effect at y_pdf."""
            chosen_hdr = "儲蓄存款 SAVINGS"
            chosen_num = ""
            for s in sections:
                if s["y"] <= y_pdf:
                    chosen_hdr = s["header"]
                    chosen_num = s.get("account_number", "")
                else:
                    break
            return chosen_hdr, chosen_num

        def _date_for_y(y_pdf: float) -> str:
            chosen = ""
            for d in dates:
                if d["y"] <= y_pdf + 3.0:
                    chosen = d["text"]
                else:
                    break
            return chosen

        def _balance_for_y(y_pdf: float) -> float | None:
            for b in balances:
                if b["y"] >= y_pdf - 5.0:
                    return b["amount"]
            return None

        def _normalize_bocom_date(raw: str) -> str:
            """Convert YYYY/MM/DD → YYYY-MM-DD."""
            if not raw:
                return ""
            return raw.replace("/", "-")

        # Filter out summary/skip rows from prescan amounts.
        # Normalize spaces: BOCOM PDFs print spaced CJK like "交 易 總 金 額"
        # which won't match "交易總金額" without removing whitespace from both sides.
        skip_nospace = {s.lower().replace(" ", "") for s in BankStatementParser._BOCOM_SKIP_PATTERNS}

        def _section_header_for_y(y_pdf: float) -> str:
            return _section_for_y(y_pdf)[0]

        bf_by_section = BankStatementParser._bocom_v2_bf_opening_by_section(
            sections,
            amounts,
            balances,
            _section_header_for_y,
            _date_for_y,
            _normalize_bocom_date,
            header_y=float(ps.get("header_y") or 0.0),
        )
        if bf_by_section:
            logger.info(
                "[BOCOM-V2][P%d] B/F opening rows for sections: %s",
                page_num + 1,
                ", ".join(sorted(bf_by_section.keys())),
            )

        out_txns: List[Dict[str, Any]] = []

        emitted_bf_for_section: set[str] = set()
        for i, amt_rec in enumerate(amounts):
            y_pdf = amt_rec["y"]
            acct_type_eff = _section_header_for_y(float(y_pdf))
            if acct_type_eff not in emitted_bf_for_section:
                bf_row = bf_by_section.get(acct_type_eff)
                if bf_row is not None:
                    out_txns.append(dict(bf_row))
                emitted_bf_for_section.add(acct_type_eff)

            col    = amt_rec["col"]
            amount = amt_rec["amount"]

            if pymu_descs is not None:
                desc   = pymu_descs[i].strip()
                dt_raw = _date_for_y(y_pdf)
                acct_type, acct_num = _section_for_y(y_pdf)
            else:
                closest = _closest_vlm(y_pdf)
                desc      = closest.get("description", "").strip() if closest else ""
                acct_type = closest.get("account_type", "").strip() if closest else ""
                acct_num  = closest.get("account_number", "").strip() if closest else ""
                dt_raw    = closest.get("date_label", "").strip()  if closest else ""
                if not dt_raw:
                    dt_raw = _date_for_y(y_pdf)
                if not acct_type:
                    acct_type, acct_num = _section_for_y(y_pdf)

            # Skip summary/BAL-B/F rows (space-normalized for spaced CJK)
            desc_nospace = desc.lower().replace(" ", "")
            if any(kw in desc_nospace for kw in skip_nospace):
                logger.debug("[BOCOM-V2][P%d] Skipping summary row: %s", page_num + 1, desc[:80])
                continue

            txn_date = _normalize_bocom_date(dt_raw)
            balance  = _balance_for_y(y_pdf)

            txn: Dict[str, Any] = {
                "transaction_date": txn_date or None,
                "value_date":       txn_date or None,
                "description":      desc or "",
                "deposit":          amount if col == "Cr" else None,
                "withdrawal":       amount if col == "Dr" else None,
                "balance":          balance,
                "currency":         "HKD",
                "account_type":     acct_type,
                "account_number":   acct_num or None,
                "categorise":       "",
                "confidence_score": 0.90,
            }
            out_txns.append(txn)

        # Emit empty 無交易 rows for sections with no amounts
        sections_with_amounts: set[str] = set()
        for amt_rec in amounts:
            sections_with_amounts.add(_section_for_y(amt_rec["y"])[0])

        for sec in sections:
            if sec["header"] not in sections_with_amounts:
                sec_balance = _balance_for_y(sec["y"])
                empty_txn: Dict[str, Any] = {
                    "transaction_date": None,
                    "value_date":       None,
                    "description":      "無交易",
                    "deposit":          None,
                    "withdrawal":       None,
                    "balance":          sec_balance,
                    "currency":         "HKD",
                    "account_type":     sec["header"],
                    "account_number":   sec.get("account_number") or None,
                    "categorise":       "",
                    "confidence_score": 1.0,
                }
                out_txns.append(empty_txn)

        logger.info(
            "[BOCOM-V2][P%d] Merged %d transactions (%d empty-section rows)",
            page_num + 1, len(out_txns),
            sum(1 for t in out_txns if t["description"] == "無交易"),
        )
        return await self._bocom_apply_ar_manager_if_enabled(
            page,
            page_num,
            out_txns,
            page_verification_out,
            company_identity,
        )

    async def _parse_bocom_statement(
        self,
        file_path: str,
        full_text: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        page_verification_out: Dict[int, str] | None = None,
    ) -> List[Dict]:
        """Parse BOCOM (Bank of Communications HK) statements.

        V2 (prescan-driven):  PyMuPDF extracts amounts + descriptions; VLM only for scans.
        V1 (VLM-only):        Full dual-track VLM pipeline via _parse_with_ocr_fallback.
        """
        import fitz

        _use_v2 = os.getenv("BOCOM_PIPELINE_V2", "false").lower() in ("1", "true", "yes")
        logger.info(
            "[BOCOM] Pipeline version: %s",
            "V2 (prescan-driven)" if _use_v2 else "V1 (VLM-only)",
        )

        if _use_v2:
            from app.ocr.runtime import BANK_VLM_MODEL
            doc = fitz.open(file_path)
            all_txns: List[Dict] = []

            for page_num in range(len(doc)):
                page = doc[page_num]
                logger.info("[BOCOM] Processing page %d/%d (V2)", page_num + 1, len(doc))
                self._emit_progress(
                    progress_callback,
                    percent=min(80, 20 + int(((page_num + 1) / max(len(doc), 1)) * 60)),
                    label=f"BOCOM V2 處理中（第 {page_num + 1}/{len(doc)} 頁）",
                    page_current=page_num + 1,
                    page_total=len(doc),
                )
                try:
                    _word_count = len(page.get_text("words"))
                    _SCANNED_THRESHOLD = 20
                    if _word_count < _SCANNED_THRESHOLD:
                        logger.info(
                            "[BOCOM-V2][P%d] Only %d words (<%d) — scanned page, "
                            "will be handled by V1 VLM fallback",
                            page_num + 1, _word_count, _SCANNED_THRESHOLD,
                        )
                        continue

                    page_txns = await self._bocom_process_page_v2(
                        page=page,
                        page_num=page_num,
                        page_count=len(doc),
                        vlm_model=BANK_VLM_MODEL,
                        company_identity=company_identity,
                        page_verification_out=page_verification_out,
                    )
                    for txn in page_txns:
                        txn["_page"] = page_num + 1
                    if page_txns:
                        logger.info("✅ [BOCOM-V2] Page %d: %d transactions", page_num + 1, len(page_txns))
                    else:
                        logger.warning("⚠️ [BOCOM-V2] Page %d: no transactions", page_num + 1)
                    all_txns.extend(page_txns)
                except Exception as page_err:
                    logger.error("[BOCOM-V2] Page %d error: %s", page_num + 1, page_err, exc_info=True)

            if all_txns:
                logger.info("[BOCOM-V2] Total: %d transactions from V2 pipeline", len(all_txns))
                return all_txns

            logger.warning(
                "[BOCOM-V2] V2 found 0 transactions across %d pages — "
                "falling back to full V1 VLM pipeline",
                len(doc),
            )

        # V1 fallback: full VLM-only dual-track pipeline
        logger.info("[BOCOM] Using V1 VLM-only pipeline (full VLM backup)")
        return await self._parse_with_ocr_fallback(
            file_path,
            "BOCOM",
            company_identity=company_identity,
            progress_callback=progress_callback,
            page_verification_out=page_verification_out,
        )

    async def _parse_generic_statement(
        self,
        file_path: str,
        full_text: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        page_verification_out: Dict[int, str] | None = None,
    ) -> List[Dict]:
        """Generic parser for unknown bank formats"""
        logger.info("Using generic parser")
        
        # Try OCR-based extraction
        transactions = await self._parse_with_ocr_fallback(
            file_path,
            'GENERIC',
            company_identity=company_identity,
            progress_callback=progress_callback,
            page_verification_out=page_verification_out,
        )
        
        return transactions
    
    @staticmethod
    def _extract_json_from_vlm_output(raw_text: str) -> Any:
        """
        Robustly extract the outermost JSON object from VLM output.

        Strategy:
          1. Strip markdown code fences (backtick-json prefix and suffix) with regex.
          2. Find first-brace to last-brace slice and try json.loads.
          3. If that fails (e.g. VLM truncated the response), attempt partial recovery:
             scan for complete transaction objects inside the transactions array and
             return whatever complete entries were found.
          4. On total failure, return raw_text dict for the TSV fallback path.
        """
        import json as _json
        import re as _re

        raw = (raw_text or "").strip()

        # Strip leading code fence: ```json or ```
        clean = _re.sub(r'^```(?:json)?\s*\n?', '', raw).strip()
        # Strip trailing code fence: ```
        clean = _re.sub(r'\n?\s*```\s*$', '', clean).strip()

        first_brace = clean.find('{')
        last_brace = clean.rfind('}')
        if first_brace != -1 and last_brace > first_brace:
            json_str = clean[first_brace: last_brace + 1]
        else:
            json_str = clean

        # --- Pre-processing: strip comma thousand-separators from numbers ---
        # VLMs sometimes emit "balance": 92,675.27 despite being told not to.
        # json.loads rejects commas inside numbers, so we sanitise before parsing.
        # Pattern: digit group followed by ,NNN (thousands group) — match the whole
        # number and drop internal commas.  Run in a loop for numbers like 1,234,567.
        def _strip_number_commas(s: str) -> str:
            prev = ""
            while prev != s:
                prev = s
                s = _re.sub(r'\b(\d{1,3}),(\d{3})(?=[,.\D]|$)', r'\1\2', s)
            return s
        json_str = _strip_number_commas(json_str)

        # --- Attempt 1: full parse ---
        try:
            return _json.loads(json_str)
        except Exception as e:
            logger.warning(
                f"[VLM JSON] Full parse failed ({len(json_str)} chars): {e}. "
                f"First 200: {json_str[:200]!r}"
            )

        # --- Attempt 2: partial recovery for truncated responses ---
        # The VLM sometimes hits a token limit mid-JSON. We scan for complete
        # transaction objects inside the transactions array and return those.
        try:
            m = _re.search(r'"transactions"\s*:\s*\[', clean)
            if not m:
                return {'raw_text': json_str}

            pos = m.end()
            transactions: list = []

            while pos < len(clean):
                # Skip whitespace and commas between objects
                while pos < len(clean) and clean[pos] in ' \t\n\r,':
                    pos += 1
                if pos >= len(clean) or clean[pos] != '{':
                    break

                # Walk forward tracking brace depth (respecting strings)
                depth = 0
                in_string = False
                escaped = False
                end_pos = -1
                for i in range(pos, len(clean)):
                    ch = clean[i]
                    if escaped:
                        escaped = False
                        continue
                    if ch == '\\' and in_string:
                        escaped = True
                        continue
                    if ch == '"':
                        in_string = not in_string
                        continue
                    if in_string:
                        continue
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end_pos = i
                            break

                if end_pos == -1:
                    break  # incomplete object — stop scanning

                obj_str = clean[pos: end_pos + 1]
                try:
                    obj = _json.loads(obj_str)
                    transactions.append(obj)
                except Exception:
                    pass  # skip malformed object
                pos = end_pos + 1

            if transactions:
                logger.info(
                    f"[VLM JSON] Partial recovery: {len(transactions)} complete transaction object(s)"
                )
                return {"transactions": transactions}

            logger.warning("[VLM JSON] Partial recovery found no complete transaction objects")
        except Exception as recovery_err:
            logger.warning(f"[VLM JSON] Partial recovery failed: {recovery_err}")

        return {'raw_text': json_str}

    @staticmethod
    def _count_arith_violations(transactions: List[Dict[str, Any]]) -> int:
        """
        Count consecutive balance arithmetic violations for a list of transactions.

        For each consecutive pair of same-account-type transactions that carry an
        explicit deposit or withdrawal, verify:
            deposit:    balance_new ≈ balance_prev + deposit
            withdrawal: balance_new ≈ balance_prev - withdrawal

        Returns the number of pairs whose arithmetic does not check out (tolerance 0.02).
        Used both inside _score_vlm_result (Penalty 3) and as a standalone signal to
        decide whether a page needs a Round-2 retry.
        """
        valid_txns = [
            t for t in transactions
            if (t.get("存入") or t.get("received") or t.get("deposit"))
            or (t.get("提取") or t.get("spent") or t.get("withdrawal"))
        ]
        violations = 0
        _prev_bal: float | None = None
        _prev_acct: str = "__init__"
        for t in valid_txns:
            _b_r = t.get("原幣結餘") or t.get("balance") or t.get("結餘")
            _d_r = t.get("存入") or t.get("received") or t.get("deposit")
            _w_r = t.get("提取") or t.get("spent") or t.get("withdrawal")
            _ac  = str(t.get("賬戶類型") or t.get("account_type") or "").strip()
            try:
                _cb = round(float(str(_b_r).replace(",", "")), 2) if _b_r else None
                _d  = round(float(str(_d_r).replace(",", "")), 2) if _d_r else None
                _w  = round(float(str(_w_r).replace(",", "")), 2) if _w_r else None
            except (ValueError, TypeError):
                _cb = _d = _w = None
            if _prev_bal is not None and _cb is not None and _ac == _prev_acct:
                if _d and _d > 0 and abs(round(_prev_bal + _d, 2) - _cb) > 0.02:
                    violations += 1
                elif _w and _w > 0 and abs(round(_prev_bal - _w, 2) - _cb) > 0.02:
                    violations += 1
            if _cb is not None:
                _prev_bal = _cb
                _prev_acct = _ac
        return violations

    @staticmethod
    def _score_vlm_result(transactions: List[Dict[str, Any]]) -> float:
        """
        Score a list of extracted transactions.

        Tier 1 (primary)  — rows with an explicit deposit or withdrawal amount.
                            Score = count + avg_confidence (e.g. 10 txns → ~10.9).
        Tier 2 (secondary) — rows with a valid running balance but no explicit amount.
                            Score = count × 0.1 (e.g. 10 txns → 1.0).
                            These can still be useful: _reconcile_amounts_by_balance
                            will derive amounts from balance deltas at normalisation time.
        Tier 3 — nothing useful → 0.0 (NONE).

        A Tier-1 track always outscores any Tier-2 track, so the arbitrator will
        prefer a track with real amounts over a balance-only track.
        """
        if not transactions:
            return 0.0

        # Tier 1: explicit amounts
        valid_txns = [
            t for t in transactions
            if (t.get("存入") or t.get("received") or t.get("deposit"))
            or (t.get("提取") or t.get("spent") or t.get("withdrawal"))
        ]
        count_score = len(valid_txns)
        if count_score > 0:
            raw_conf = [t.get("confidence_score") or t.get("信心度") for t in valid_txns]
            conf_vals = [float(c) for c in raw_conf if c is not None and str(c).strip() != ""]
            avg_conf = sum(conf_vals) / len(conf_vals) if conf_vals else 0.8
            base_score = count_score + avg_conf

            # Penalty 1 — duplicate running balances (echo / fabrication signal).
            # Rows from PORTFOLIO SUMMARY / ACCOUNT SUMMARY have no matching running balance
            # in the real transaction sequence, so the same balance appearing twice strongly
            # indicates hallucinated rows. Each duplicate subtracts 2.0.
            seen_balances: set = set()
            dup_count = 0
            for t in valid_txns:
                bal_raw = t.get("原幣結餘") or t.get("balance") or t.get("結餘")
                try:
                    bal_key = round(float(str(bal_raw).replace(",", "")), 2) if bal_raw else None
                except (ValueError, TypeError):
                    bal_key = None
                if bal_key is not None:
                    if bal_key in seen_balances:
                        dup_count += 1
                    else:
                        seen_balances.add(bal_key)

            # Penalty 2 — missing transaction dates.
            # Real transaction rows always have a date. Rows extracted from non-transaction
            # sections (PORTFOLIO SUMMARY, ACCOUNT SUMMARY, etc.) typically have no date.
            # Each dateless row subtracts 1.5.
            def _has_date(t: Dict[str, Any]) -> bool:
                val = (t.get("date") or t.get("transaction_date")
                       or t.get("日期") or t.get("交易日期") or "")
                return bool(str(val).strip())

            dateless_count = sum(1 for t in valid_txns if not _has_date(t))

            # Penalty 3 — balance arithmetic violations (delegates to shared helper).
            arith_violations = BankStatementParser._count_arith_violations(valid_txns)

            penalty = dup_count * 2.0 + dateless_count * 1.5 + arith_violations * 3.0
            return max(0.0, base_score - penalty)

        # Tier 2: balance-only rows (reconciliation can fill amounts from balance deltas)
        def _has_valid_balance(t: Dict[str, Any]) -> bool:
            val = t.get("原幣結餘") or t.get("balance") or t.get("結餘")
            if val is None:
                return False
            try:
                return float(str(val).replace(",", "")) > 0
            except (ValueError, TypeError):
                return False

        balance_count = sum(1 for t in transactions if _has_valid_balance(t))
        return balance_count * 0.1

    # ── Page Density Classifier ───────────────────────────────────────────────────
    # Classifies a fitz.Page as SPARSE / DENSE / UNKNOWN before VLM dispatch.
    # Used by process_page to select image parameters and (optionally) chunking path.
    # Must never raise — always returns a safe dict on any failure.
    _DENSITY_TEXT_THRESHOLD = 12      # estimated_rows >= this → DENSE (SCB dense ~12–20 rows)
    _DENSITY_MIN_LINES = 5            # fewer text lines → fall back to image analysis
    _DENSITY_CONFIDENCE_GATE = 0.55   # below this confidence → treat as UNKNOWN

    @staticmethod
    def _classify_page_density(page) -> dict:
        """Classify a fitz.Page as SPARSE / DENSE / UNKNOWN.

        Strategy (cheapest first):
          1. Text-layer analysis via page.get_text() — free, reliable for text PDFs.
          2. Image-density analysis via grayscale pixmap — fallback for scanned pages.
          3. UNKNOWN fallback when both fail.

        Returns:
            {"level": "SPARSE"|"DENSE"|"UNKNOWN",
             "confidence": float,     # 0.0–1.0
             "estimated_rows": int,   # proxy for transaction row count
             "method": "text"|"image"|"fallback"}
        """
        import re as _re

        # ── 1. Text-layer analysis ────────────────────────────────────────────
        try:
            text = page.get_text() or ""
            lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
            n_lines = len(lines)

            if n_lines >= BankStatementParser._DENSITY_MIN_LINES:
                date_re = _re.compile(
                    r'\b\d{1,2}[\-/]\d{1,2}[\-/]\d{2,4}\b'
                    r'|\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b',
                    _re.IGNORECASE,
                )
                date_hits = len(date_re.findall(text))
                estimated_rows = max(n_lines // 4, date_hits)
                threshold = BankStatementParser._DENSITY_TEXT_THRESHOLD

                if estimated_rows >= threshold:
                    conf = round(min(0.90, 0.55 + (estimated_rows - threshold) / 100), 2)
                    return {"level": "DENSE", "confidence": conf,
                            "estimated_rows": estimated_rows, "method": "text"}
                else:
                    conf = round(min(0.88, 0.52 + (threshold - estimated_rows) / 50), 2)
                    return {"level": "SPARSE", "confidence": conf,
                            "estimated_rows": estimated_rows, "method": "text"}
        except Exception as _te:
            logger.debug(f"[DENSITY] Text analysis failed: {_te}")

        # ── 2. Image-density fallback (scanned / image-based PDF) ────────────
        try:
            import numpy as _np
            import fitz as _fitz

            pix = page.get_pixmap(
                matrix=_fitz.Matrix(0.5, 0.5),  # half-scale — only counting rows
                colorspace=_fitz.csGRAY,
            )
            img = _np.frombuffer(pix.samples, dtype=_np.uint8).reshape(pix.height, pix.width)
            h, w = img.shape
            body = img[int(h * 0.18): int(h * 0.92), :]
            dark_cols = _np.sum(body < 180, axis=1)
            text_rows = int(_np.sum(
                (dark_cols > max(8, int(w * 0.01))) & (dark_cols < int(w * 0.70))
            ))
            estimated_rows = text_rows // 12
            threshold = BankStatementParser._DENSITY_TEXT_THRESHOLD

            if estimated_rows >= threshold:
                conf = round(min(0.72, 0.42 + (estimated_rows - threshold) / 100), 2)
                return {"level": "DENSE", "confidence": conf,
                        "estimated_rows": estimated_rows, "method": "image"}
            return {"level": "SPARSE", "confidence": 0.52,
                    "estimated_rows": estimated_rows, "method": "image"}

        except Exception as _ie:
            logger.warning(f"[DENSITY] Image analysis failed: {_ie}. Defaulting to UNKNOWN.")

        return {"level": "UNKNOWN", "confidence": 0.0, "estimated_rows": 0, "method": "fallback"}

    # ── OpenCV Dual-Strategy Chunker ──────────────────────────────────────────
    # Self-contained in this file; zero imports from ocr.py or receipt pipeline.
    # Validated on actual SCB PDF: Pages 3 & 4 have 0–1 grid lines → Strategy B.

    @staticmethod
    def _cv_detect_h_lines(img_bgr: Any) -> list:
        """Detect horizontal table grid lines via morphological operations.

        Uses a wide horizontal kernel (40% of page width) to erode+dilate,
        keeping only pixel runs that span most of the page width.
        Returns sorted list of y-coordinates of detected line centres.
        """
        import cv2 as _cv2
        import numpy as _np

        gray = _cv2.cvtColor(img_bgr, _cv2.COLOR_BGR2GRAY)
        _, binary = _cv2.threshold(gray, 180, 255, _cv2.THRESH_BINARY_INV)
        h, w = binary.shape
        kernel_len = max(1, int(w * 0.40))
        h_kernel = _cv2.getStructuringElement(_cv2.MORPH_RECT, (kernel_len, 1))
        h_lines_img = _cv2.erode(binary, h_kernel, iterations=1)
        h_lines_img = _cv2.dilate(h_lines_img, h_kernel, iterations=1)
        row_sums = _np.sum(h_lines_img, axis=1)
        line_threshold = w * 0.3 * 255

        line_ys: list = []
        in_line = False
        line_start = 0
        for y, val in enumerate(row_sums):
            if val > line_threshold and not in_line:
                in_line = True
                line_start = y
            elif val <= line_threshold and in_line:
                in_line = False
                line_ys.append((line_start + y) // 2)
        return sorted(line_ys)

    @staticmethod
    def _cv_intelligent_chunk_page(
        img_bgr: Any,
        max_chunk_height_px: int = 975,
        overlap_px: int = 90,
        top_skip_ratio: float = 0.12,
        bottom_skip_ratio: float = 0.95,
        max_chunks: int = 6,
    ) -> list:
        """Dual-strategy chunking validated on SCB bank statements.

        Strategy A (primary): Morphological horizontal line detection.
          Splits at actual table grid lines — reliable for statements with
          clear ruled lines (HSBC, Hang Seng, some SCB pages).

        Strategy B (fallback): Fixed-height splits with fixed pixel overlap.
          Used when fewer than 3 grid lines are found — correct for SCB
          dense transaction pages (Pages 3 & 4 have 0 and 1 grid lines).

        Default parameters are calibrated for 300 DPI rendering:
          max_chunk_height_px=975  ≈ 15–20 transaction rows per chunk
          overlap_px=90            ≈ 2 transaction rows, prevents row cuts
          top_skip_ratio=0.12      skip page header (logo, title, account info)
          bottom_skip_ratio=0.95   skip page footer (page number, disclaimer)

        Returns list of (top_px, bottom_px) tuples, capped at max_chunks.
        Isolated from ocr.py receipt segmentation — no shared code or imports.
        """
        h, w = img_bgr.shape[:2]
        table_top = int(h * top_skip_ratio)
        table_bottom = int(h * bottom_skip_ratio)

        line_ys = BankStatementParser._cv_detect_h_lines(img_bgr)
        table_lines = [y for y in line_ys if table_top <= y <= table_bottom]

        chunks: list = []

        if len(table_lines) >= 3:
            # Strategy A: split at detected grid line boundaries.
            # When two adjacent boundaries are wider than max_chunk_height_px
            # (e.g. sparse grid lines on a dense page), sub-split that gap
            # with fixed-height slices so no single chunk exceeds the cap.
            raw_boundaries = [table_top] + table_lines + [table_bottom]
            # Pre-filter: drop interior grid lines that would produce a segment
            # shorter than min_seg_px.  These are typically the table header/
            # footer rule lines (e.g. column-header bottom border) that contain
            # no transaction rows — processing them wastes a full VLM round-trip.
            min_seg_px = max(overlap_px * 2, 150)
            boundaries = [raw_boundaries[0]]
            for _b in raw_boundaries[1:-1]:      # only interior grid lines
                if (_b - boundaries[-1]) >= min_seg_px:
                    boundaries.append(_b)
            boundaries.append(raw_boundaries[-1])  # always keep table_bottom
            for i in range(len(boundaries) - 1):
                seg_start = boundaries[i]
                seg_end = boundaries[i + 1]
                if (seg_end - seg_start) <= max_chunk_height_px:
                    y_top = max(0, seg_start - overlap_px)
                    y_bottom = min(h, seg_end + overlap_px)
                    chunks.append((y_top, y_bottom))
                else:
                    # Gap too wide — apply fixed-height sub-splitting within segment
                    y = seg_start
                    while y < seg_end:
                        y_top = max(0, y - overlap_px)
                        y_bottom = min(h, y + max_chunk_height_px + overlap_px)
                        chunks.append((y_top, y_bottom))
                        y += max_chunk_height_px
            logger.info(
                f"[CHUNK-CV] Strategy A (line-based): {len(chunks)} chunks "
                f"from {len(table_lines)} grid lines"
            )
        else:
            # Strategy B: fixed-height fallback (used for SCB Pages 3, 4)
            y = table_top
            while y < table_bottom:
                y_top = max(0, y - overlap_px)
                y_bottom = min(h, y + max_chunk_height_px + overlap_px)
                chunks.append((y_top, y_bottom))
                y += max_chunk_height_px
            logger.info(
                f"[CHUNK-CV] Strategy B (fixed-height {max_chunk_height_px}px): "
                f"{len(chunks)} chunks (grid lines={len(table_lines)})"
            )

        return chunks[:max_chunks]

    async def _vlm_recognize_page_text(
        self,
        tmp_img_path: str,
        prompt: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        max_tokens: int = 8000,
        image_options: dict | None = None,
    ) -> str:
        """Cache-check → VLM image call → raw model text (no transaction parsing)."""
        from app.ocr.runtime import ocr_service as _ocr_service

        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        cache_key = f"bank-vlm:nothink:{page_hash}:{vlm_model}:{prompt_hash}:{max_tokens}"

        page_text = self._get_cached_ocr_text(cache_key)
        if page_text is not None:
            logger.info(f"[BANK][{track_name}] Cache hit")
        else:
            effective_image_opts = image_options or {
                "max_side": 800,
                "format": "JPEG",
                "quality": 85,
            }
            logger.info(
                f"[BANK][{track_name}] Cache miss — running VLM "
                f"({vlm_model}, max_tokens={max_tokens}, "
                f"max_side={effective_image_opts.get('max_side', '?')})..."
            )

            ocr_result = await _ocr_service.recognize(
                tmp_img_path,
                provider_name=vlm_model,
                model=vlm_model,
                prompt_override=prompt,
                ocr_options={
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                image_options=effective_image_opts,
            )
            page_text = (ocr_result.text if hasattr(ocr_result, "text") else "") or ""
            self._set_cached_ocr_text(cache_key, page_text)

        logger.info(
            f"[BANK][{track_name}] VLM returned {len(page_text)} chars. "
            f"First 300: {page_text[:300]!r}"
        )
        return page_text

    async def _run_vlm_track(
        self,
        tmp_img_path: str,
        prompt: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        company_identity: Dict[str, Any] | None = None,
        max_tokens: int = 8000,
        image_options: dict | None = None,
        *,
        filter_balance_anchor_rows: bool = True,
        reconcile_mode: str = "delta",
    ) -> List[Dict[str, Any]]:
        """Run a single VLM track: cache-check → OCR call → JSON parse → transaction list.

        Args:
            image_options: Per-call override for image resize/format settings.  When None
                           the safe default (max_side=800, JPEG, quality=85) is used.
            filter_balance_anchor_rows: When False (HSBC V1), keep B/F BALANCE / opening
                rows that the generic pipeline would strip as balance anchors.
            reconcile_mode: ``delta`` (default) or ``none`` (skip balance-delta overwrite;
                OCBC applies gated policy after full-statement assemble).
        """
        page_text = await self._vlm_recognize_page_text(
            tmp_img_path,
            prompt,
            page_hash,
            vlm_model,
            track_name,
            max_tokens=max_tokens,
            image_options=image_options,
        )
        parsed_data = self._extract_json_from_vlm_output(page_text)
        return self._extract_transactions_from_ai_response(
            parsed_data,
            company_identity=company_identity,
            filter_balance_anchor_rows=filter_balance_anchor_rows,
            reconcile_mode=reconcile_mode,
        )

    async def _run_balance_checker_vlm(
        self,
        tmp_img_path: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        image_options: dict | None = None,
        max_tokens: int = 1536,
    ) -> dict[str, Any]:
        """Single full-page VLM call: balance/totals JSON only (cross-VLM model B)."""
        from app.ocr.runtime import ocr_service as _ocr_service
        from app.services.bank_vlm_balance_check import (
            BANK_BALANCE_CHECKER_PROMPT,
            parse_checker_payload,
        )

        prompt = BANK_BALANCE_CHECKER_PROMPT
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        cache_key = (
            f"bank-vlm-balance-check:{page_hash}:{vlm_model}:"
            f"{prompt_hash}:{max_tokens}"
        )

        page_text = self._get_cached_ocr_text(cache_key)
        if page_text is not None:
            logger.info(f"[BANK][{track_name}] Balance-check cache hit")
        else:
            effective_image_opts = image_options or {
                "max_side": 800,
                "format": "JPEG",
                "quality": 85,
            }
            logger.info(
                f"[BANK][{track_name}] Balance-check cache miss — VLM "
                f"({vlm_model}, max_tokens={max_tokens}, "
                f"max_side={effective_image_opts.get('max_side', '?')})..."
            )
            ocr_result = await _ocr_service.recognize(
                tmp_img_path,
                provider_name=vlm_model,
                model=vlm_model,
                prompt_override=prompt,
                ocr_options={
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                image_options=effective_image_opts,
            )
            page_text = (ocr_result.text if hasattr(ocr_result, "text") else "") or ""
            self._set_cached_ocr_text(cache_key, page_text)

        logger.info(
            f"[BANK][{track_name}] Balance-check VLM returned {len(page_text)} chars. "
            f"First 300: {page_text[:300]!r}"
        )
        parsed_data = self._extract_json_from_vlm_output(page_text)
        return parse_checker_payload(parsed_data)

    async def _run_hsbc_ar_manager_vlm(
        self,
        tmp_img_path: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        prompt_full: str,
        company_identity: Dict[str, Any] | None,
        image_options: dict | None,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """Full-page HSBC AR manager pass (model B): image + draft JSON → transactions."""
        from app.ocr.runtime import ocr_service as _ocr_service

        prompt_h = hashlib.sha256(prompt_full.encode("utf-8")).hexdigest()[:16]
        cache_key = (
            f"bank-vlm-hsbc-manager:{page_hash}:{vlm_model}:"
            f"{prompt_h}:{max_tokens}"
        )

        cache_hit_flag = False
        page_text = self._get_cached_ocr_text(cache_key)
        if page_text is not None:
            cache_hit_flag = True
            logger.info(f"[BANK][{track_name}] HSBC AR manager cache hit")
        else:
            effective_image_opts = image_options or {
                "max_side": 800,
                "format": "JPEG",
                "quality": 85,
            }
            logger.info(
                f"[BANK][{track_name}] HSBC AR manager cache miss — VLM "
                f"({vlm_model}, max_tokens={max_tokens}, "
                f"max_side={effective_image_opts.get('max_side', '?')})..."
            )
            ocr_result = await _ocr_service.recognize(
                tmp_img_path,
                provider_name=vlm_model,
                model=vlm_model,
                prompt_override=prompt_full,
                ocr_options={
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                image_options=effective_image_opts,
            )
            page_text = (ocr_result.text if hasattr(ocr_result, "text") else "") or ""
            self._set_cached_ocr_text(cache_key, page_text)

        logger.info(
            f"[BANK][{track_name}] HSBC AR manager VLM returned {len(page_text)} chars. "
            f"First 300: {page_text[:300]!r}"
        )
        parsed_data = self._extract_json_from_vlm_output(page_text)
        raw_list = (
            parsed_data.get("transactions")
            if isinstance(parsed_data, dict)
            else None
        )
        if not isinstance(raw_list, list):
            raw_list = []
        mgr_out = self._extract_transactions_from_ai_response(
            parsed_data,
            company_identity=company_identity,
            filter_balance_anchor_rows=False,
        )
        logger.debug(
            "[HSBC-AR-MGR] Manager VLM parsed %d raw rows into %d transactions (cache_hit=%s)",
            len(raw_list),
            len(mgr_out),
            cache_hit_flag,
        )
        return mgr_out

    async def _run_bea_ar_manager_vlm(
        self,
        tmp_img_path: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        prompt_full: str,
        company_identity: Dict[str, Any] | None,
        image_options: dict | None,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """BEA cross-VLM AR manager: page image + bookkeeper draft JSON → transactions."""
        from app.ocr.runtime import ocr_service as _ocr_service

        prompt_h = hashlib.sha256(prompt_full.encode("utf-8")).hexdigest()[:16]
        cache_key = (
            f"bank-vlm-bea-manager:{page_hash}:{vlm_model}:"
            f"{prompt_h}:{max_tokens}"
        )

        cache_hit_flag = False
        page_text = self._get_cached_ocr_text(cache_key)
        if page_text is not None:
            cache_hit_flag = True
            logger.info("[BANK][%s] BEA AR manager cache hit", track_name)
        else:
            effective_image_opts = image_options or {
                "max_side": 800,
                "format": "JPEG",
                "quality": 85,
            }
            logger.info(
                "[BANK][%s] BEA AR manager cache miss — VLM (%s, max_tokens=%s)...",
                track_name,
                vlm_model,
                max_tokens,
            )
            ocr_result = await _ocr_service.recognize(
                tmp_img_path,
                provider_name=vlm_model,
                model=vlm_model,
                prompt_override=prompt_full,
                ocr_options={
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                image_options=effective_image_opts,
            )
            page_text = (ocr_result.text if hasattr(ocr_result, "text") else "") or ""
            self._set_cached_ocr_text(cache_key, page_text)

        logger.info(
            "[BANK][%s] BEA AR manager VLM returned %d chars.",
            track_name,
            len(page_text or ""),
        )
        parsed_data = self._extract_json_from_vlm_output(page_text)
        mgr_out = self._extract_transactions_from_ai_response(
            parsed_data,
            company_identity=company_identity,
            filter_balance_anchor_rows=False,
        )
        logger.debug(
            "[BEA-AR-MGR] cache_hit=%s extracted_len=%d",
            cache_hit_flag,
            len(mgr_out),
        )
        return mgr_out

    async def _run_boc_ar_manager_vlm(
        self,
        tmp_img_path: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        prompt_full: str,
        company_identity: Dict[str, Any] | None,
        image_options: dict | None,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """BOC cross-VLM AR manager: page image + bookkeeper draft JSON → transactions."""
        from app.ocr.runtime import ocr_service as _ocr_service

        prompt_h = hashlib.sha256(prompt_full.encode("utf-8")).hexdigest()[:16]
        cache_key = (
            f"bank-vlm-boc-manager:{page_hash}:{vlm_model}:"
            f"{prompt_h}:{max_tokens}"
        )

        cache_hit_flag = False
        page_text = self._get_cached_ocr_text(cache_key)
        if page_text is not None:
            cache_hit_flag = True
            logger.info("[BANK][%s] BOC AR manager cache hit", track_name)
        else:
            effective_image_opts = image_options or {
                "max_side": 800,
                "format": "JPEG",
                "quality": 85,
            }
            logger.info(
                "[BANK][%s] BOC AR manager cache miss — VLM (%s, max_tokens=%s)...",
                track_name,
                vlm_model,
                max_tokens,
            )
            ocr_result = await _ocr_service.recognize(
                tmp_img_path,
                provider_name=vlm_model,
                model=vlm_model,
                prompt_override=prompt_full,
                ocr_options={
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                image_options=effective_image_opts,
            )
            page_text = (ocr_result.text if hasattr(ocr_result, "text") else "") or ""
            self._set_cached_ocr_text(cache_key, page_text)

        logger.info(
            "[BANK][%s] BOC AR manager VLM returned %d chars.",
            track_name,
            len(page_text or ""),
        )
        parsed_data = self._extract_json_from_vlm_output(page_text)
        mgr_out = self._extract_transactions_from_ai_response(
            parsed_data,
            company_identity=company_identity,
            filter_balance_anchor_rows=False,
        )
        logger.debug(
            "[BOC-AR-MGR] cache_hit=%s extracted_len=%d",
            cache_hit_flag,
            len(mgr_out),
        )
        return mgr_out

    async def _run_bocom_ar_manager_vlm(
        self,
        tmp_img_path: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        prompt_full: str,
        company_identity: Dict[str, Any] | None,
        image_options: dict | None,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """BOCOM cross-VLM AR manager: page image + bookkeeper draft JSON → transactions."""
        from app.ocr.runtime import ocr_service as _ocr_service

        prompt_h = hashlib.sha256(prompt_full.encode("utf-8")).hexdigest()[:16]
        cache_key = (
            f"bank-vlm-bocom-manager:{page_hash}:{vlm_model}:"
            f"{prompt_h}:{max_tokens}"
        )

        cache_hit_flag = False
        page_text = self._get_cached_ocr_text(cache_key)
        if page_text is not None:
            cache_hit_flag = True
            logger.info("[BANK][%s] BOCOM AR manager cache hit", track_name)
        else:
            effective_image_opts = image_options or {
                "max_side": 800,
                "format": "JPEG",
                "quality": 85,
            }
            logger.info(
                "[BANK][%s] BOCOM AR manager cache miss — VLM (%s, max_tokens=%s)...",
                track_name,
                vlm_model,
                max_tokens,
            )
            ocr_result = await _ocr_service.recognize(
                tmp_img_path,
                provider_name=vlm_model,
                model=vlm_model,
                prompt_override=prompt_full,
                ocr_options={
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                image_options=effective_image_opts,
            )
            page_text = (ocr_result.text if hasattr(ocr_result, "text") else "") or ""
            self._set_cached_ocr_text(cache_key, page_text)

        logger.info(
            "[BANK][%s] BOCOM AR manager VLM returned %d chars.",
            track_name,
            len(page_text or ""),
        )
        parsed_data = self._extract_json_from_vlm_output(page_text)
        mgr_out = self._extract_transactions_from_ai_response(
            parsed_data,
            company_identity=company_identity,
            filter_balance_anchor_rows=False,
        )
        logger.debug(
            "[BOCOM-AR-MGR] cache_hit=%s extracted_len=%d",
            cache_hit_flag,
            len(mgr_out),
        )
        return mgr_out

    async def _run_scb_ar_manager_vlm(
        self,
        tmp_img_path: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        prompt_full: str,
        company_identity: Dict[str, Any] | None,
        image_options: dict | None,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """SCB cross-VLM AR manager: page image + bookkeeper draft JSON → transactions."""
        from app.ocr.runtime import ocr_service as _ocr_service

        prompt_h = hashlib.sha256(prompt_full.encode("utf-8")).hexdigest()[:16]
        cache_key = (
            f"bank-vlm-scb-manager:{page_hash}:{vlm_model}:"
            f"{prompt_h}:{max_tokens}"
        )

        cache_hit_flag = False
        page_text = self._get_cached_ocr_text(cache_key)
        if page_text is not None:
            cache_hit_flag = True
            logger.info("[BANK][%s] SCB AR manager cache hit", track_name)
        else:
            effective_image_opts = image_options or {
                "max_side": 800,
                "format": "JPEG",
                "quality": 85,
            }
            logger.info(
                "[BANK][%s] SCB AR manager cache miss — VLM (%s, max_tokens=%s)...",
                track_name,
                vlm_model,
                max_tokens,
            )
            ocr_result = await _ocr_service.recognize(
                tmp_img_path,
                provider_name=vlm_model,
                model=vlm_model,
                prompt_override=prompt_full,
                ocr_options={
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                image_options=effective_image_opts,
            )
            page_text = (ocr_result.text if hasattr(ocr_result, "text") else "") or ""
            self._set_cached_ocr_text(cache_key, page_text)

        logger.info(
            "[BANK][%s] SCB AR manager VLM returned %d chars.",
            track_name,
            len(page_text or ""),
        )
        parsed_data = self._extract_json_from_vlm_output(page_text)
        mgr_out = self._extract_transactions_from_ai_response(
            parsed_data,
            company_identity=company_identity,
            filter_balance_anchor_rows=False,
        )
        logger.debug(
            "[SCB-AR-MGR] cache_hit=%s extracted_len=%d",
            cache_hit_flag,
            len(mgr_out),
        )
        return mgr_out

    async def _run_hang_seng_ar_manager_vlm(
        self,
        tmp_img_path: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        prompt_full: str,
        company_identity: Dict[str, Any] | None,
        image_options: dict | None,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """Hang Seng cross-VLM AR manager: page image + bookkeeper draft JSON."""
        from app.ocr.runtime import ocr_service as _ocr_service

        prompt_h = hashlib.sha256(prompt_full.encode("utf-8")).hexdigest()[:16]
        cache_key = (
            f"bank-vlm-hang-seng-manager:{page_hash}:{vlm_model}:"
            f"{prompt_h}:{max_tokens}"
        )

        cache_hit_flag = False
        page_text = self._get_cached_ocr_text(cache_key)
        if page_text is not None:
            cache_hit_flag = True
            logger.info("[BANK][%s] Hang Seng AR manager cache hit", track_name)
        else:
            effective_image_opts = image_options or {
                "max_side": 800,
                "format": "JPEG",
                "quality": 85,
            }
            logger.info(
                "[BANK][%s] Hang Seng AR manager cache miss — VLM (%s, max_tokens=%s)...",
                track_name,
                vlm_model,
                max_tokens,
            )
            ocr_result = await _ocr_service.recognize(
                tmp_img_path,
                provider_name=vlm_model,
                model=vlm_model,
                prompt_override=prompt_full,
                ocr_options={
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                image_options=effective_image_opts,
            )
            page_text = (ocr_result.text if hasattr(ocr_result, "text") else "") or ""
            self._set_cached_ocr_text(cache_key, page_text)

        logger.info(
            "[BANK][%s] Hang Seng AR manager VLM returned %d chars.",
            track_name,
            len(page_text or ""),
        )
        parsed_data = self._extract_json_from_vlm_output(page_text)
        mgr_out = self._extract_transactions_from_ai_response(
            parsed_data,
            company_identity=company_identity,
            filter_balance_anchor_rows=False,
        )
        logger.debug(
            "[HANG-SENG-AR-MGR] cache_hit=%s extracted_len=%d",
            cache_hit_flag,
            len(mgr_out),
        )
        return mgr_out

    async def _run_ocbc_ar_manager_vlm(
        self,
        tmp_img_path: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        prompt_full: str,
        company_identity: Dict[str, Any] | None,
        image_options: dict | None,
        max_tokens: int = 8000,
    ) -> List[Dict[str, Any]]:
        """OCBC cross-VLM AR manager: page image + bookkeeper draft JSON → transactions."""
        from app.ocr.runtime import ocr_service as _ocr_service

        prompt_h = hashlib.sha256(prompt_full.encode("utf-8")).hexdigest()[:16]
        cache_key = (
            f"bank-vlm-ocbc-manager:{page_hash}:{vlm_model}:"
            f"{prompt_h}:{max_tokens}"
        )

        cache_hit_flag = False
        page_text = self._get_cached_ocr_text(cache_key)
        if page_text is not None:
            cache_hit_flag = True
            logger.info("[BANK][%s] OCBC AR manager cache hit", track_name)
        else:
            effective_image_opts = image_options or {
                "max_side": 800,
                "format": "JPEG",
                "quality": 85,
            }
            logger.info(
                "[BANK][%s] OCBC AR manager cache miss — VLM (%s, max_tokens=%s)...",
                track_name,
                vlm_model,
                max_tokens,
            )
            ocr_result = await _ocr_service.recognize(
                tmp_img_path,
                provider_name=vlm_model,
                model=vlm_model,
                prompt_override=prompt_full,
                ocr_options={
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                    "enable_thinking": False,
                },
                image_options=effective_image_opts,
            )
            page_text = (ocr_result.text if hasattr(ocr_result, "text") else "") or ""
            self._set_cached_ocr_text(cache_key, page_text)

        logger.info(
            "[BANK][%s] OCBC AR manager VLM returned %d chars.",
            track_name,
            len(page_text or ""),
        )
        parsed_data = self._extract_json_from_vlm_output(page_text)
        mgr_out = self._extract_transactions_from_ai_response(
            parsed_data,
            company_identity=company_identity,
            filter_balance_anchor_rows=False,
            reconcile_mode="none",
        )
        logger.debug(
            "[OCBC-AR-MGR] cache_hit=%s extracted_len=%d",
            cache_hit_flag,
            len(mgr_out),
        )
        return mgr_out

    async def _hsbc_apply_ar_manager_if_enabled(
        self,
        page,
        page_num: int,
        page_txns: List[Dict[str, Any]],
        page_verification_out: Dict[int, str] | None,
        company_identity: Dict[str, Any] | None,
    ) -> List[Dict[str, Any]]:
        """Optional second VLM (BANK_CROSS_VLM_*): merge manager rows into bookkeeper."""
        cross_on = os.getenv("BANK_CROSS_VLM_VERIFY", "").lower() in (
            "1",
            "true",
            "yes",
        )
        if not cross_on:
            return page_txns
        from app.core.config import require_bank_cross_vlm_settings

        cross_cfg = require_bank_cross_vlm_settings()
        if cross_cfg is None:
            return page_txns
        cross_model = cross_cfg["model"]

        if not page_txns:
            return page_txns

        from app.bank_prompts.hsbc import HSBC_AR_MANAGER_PROMPT_PREFIX
        from app.services.bank_vlm_hsbc_manager_merge import (
            build_bookkeeper_snapshot,
            merge_manager_into_bookkeeper,
        )

        max_tokens = max(512, int(os.getenv("HSBC_MANAGER_MAX_TOKENS", "4096")))
        desc_max = max(20, int(os.getenv("HSBC_MANAGER_DESC_MAX_CHARS", "100")))
        full_if_n = max(1, int(os.getenv("HSBC_MANAGER_FULL_JSON_MAX_ROWS", "15")))
        tol = float(os.getenv("HSBC_MANAGER_AMOUNT_TOLERANCE", "0.02"))

        tmp_path, page_hash, image_opts = self._hsbc_write_annotated_page_jpeg(
            page, page_num
        )
        try:
            snapshot = build_bookkeeper_snapshot(
                page_txns, desc_max=desc_max, full_if_n_rows=full_if_n
            )
            prompt_full = (
                HSBC_AR_MANAGER_PROMPT_PREFIX
                + "\n\nBOOKKEEPER_DRAFT_JSON:\n"
                + snapshot
            )
            manager_txns = await self._run_hsbc_ar_manager_vlm(
                tmp_path,
                page_hash,
                cross_model,
                "HSBC-AR-MGR",
                prompt_full,
                company_identity,
                image_opts,
                max_tokens=max_tokens,
            )
            merged, misaligned, per_ok = merge_manager_into_bookkeeper(
                page_txns, manager_txns, amount_tolerance=tol
            )
            page_needs = misaligned or any(not x for x in per_ok)
            logger.debug(
                "[HSBC-AR-MGR] Page %d merge complete: merged=%d manager=%d needs_review=%s",
                page_num + 1,
                len(merged),
                len(manager_txns),
                page_needs,
            )
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = (
                    "needs_review" if page_needs else "verified"
                )
            for i, _row in enumerate(merged):
                bad = misaligned or (i < len(per_ok) and not per_ok[i])
                _row["_ar_manager_status"] = "needs_review" if bad else "verified"
            return merged
        except Exception as mgr_err:
            logger.warning(
                "[HSBC-AR-MGR] Page %d failed: %s",
                page_num + 1,
                mgr_err,
                exc_info=True,
            )
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = "needs_review"
            for _row in page_txns:
                _row["_ar_manager_status"] = "error"
            return page_txns
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    async def _bea_apply_ar_manager_if_enabled(
        self,
        page,
        page_num: int,
        page_txns: List[Dict[str, Any]],
        page_verification_out: Dict[int, str] | None,
        company_identity: Dict[str, Any] | None,
    ) -> List[Dict[str, Any]]:
        """Optional second VLM (BANK_CROSS_VLM_*): BEA balance-only merge like HSBC."""
        cross_on = os.getenv("BANK_CROSS_VLM_VERIFY", "").lower() in (
            "1",
            "true",
            "yes",
        )
        cross_model = os.getenv("BANK_CROSS_VLM_MODEL", "").strip()
        if not cross_on:
            return page_txns
        if not cross_model:
            raise RuntimeError(
                "BANK_CROSS_VLM_MODEL must be set in the environment when "
                "BANK_CROSS_VLM_VERIFY is enabled."
            )
        if not page_txns:
            return page_txns

        from app.bank_prompts.bea import BEA_AR_MANAGER_PROMPT_PREFIX
        from app.services.bank_vlm_hsbc_manager_merge import (
            build_bookkeeper_snapshot,
            merge_manager_into_bookkeeper,
        )

        max_tokens = max(512, int(os.getenv("HSBC_MANAGER_MAX_TOKENS", "4096")))
        desc_max = max(20, int(os.getenv("HSBC_MANAGER_DESC_MAX_CHARS", "100")))
        full_if_n = max(1, int(os.getenv("HSBC_MANAGER_FULL_JSON_MAX_ROWS", "15")))
        tol = float(os.getenv("HSBC_MANAGER_AMOUNT_TOLERANCE", "0.02"))

        tmp_path, page_hash, image_opts = self._bea_write_page_jpeg_for_manager(
            page, page_num
        )
        try:
            snapshot = build_bookkeeper_snapshot(
                page_txns, desc_max=desc_max, full_if_n_rows=full_if_n
            )
            prompt_full = (
                BEA_AR_MANAGER_PROMPT_PREFIX
                + "\n\nBOOKKEEPER_DRAFT_JSON:\n"
                + snapshot
            )
            manager_txns = await self._run_bea_ar_manager_vlm(
                tmp_path,
                page_hash,
                cross_model,
                "BEA-AR-MGR",
                prompt_full,
                company_identity,
                image_opts,
                max_tokens=max_tokens,
            )
            merged, misaligned, per_ok = merge_manager_into_bookkeeper(
                page_txns, manager_txns, amount_tolerance=tol
            )
            page_needs = misaligned or any(not x for x in per_ok)
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = (
                    "needs_review" if page_needs else "verified"
                )
            for i, _row in enumerate(merged):
                bad = misaligned or (i < len(per_ok) and not per_ok[i])
                _row["_ar_manager_status"] = "needs_review" if bad else "verified"
            return merged
        except Exception as mgr_err:
            logger.warning(
                "[BEA-AR-MGR] Page %d failed: %s",
                page_num + 1,
                mgr_err,
                exc_info=True,
            )
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = "needs_review"
            for _row in page_txns:
                _row["_ar_manager_status"] = "error"
            return page_txns
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    async def _boc_apply_ar_manager_if_enabled(
        self,
        page,
        page_num: int,
        page_txns: List[Dict[str, Any]],
        page_verification_out: Dict[int, str] | None,
        company_identity: Dict[str, Any] | None,
    ) -> List[Dict[str, Any]]:
        """Optional second VLM (BANK_CROSS_VLM_*): BOC balance-only merge like BEA/HSBC."""
        cross_on = os.getenv("BANK_CROSS_VLM_VERIFY", "").lower() in (
            "1",
            "true",
            "yes",
        )
        cross_model = os.getenv("BANK_CROSS_VLM_MODEL", "").strip()
        if not cross_on:
            return page_txns
        if not cross_model:
            raise RuntimeError(
                "BANK_CROSS_VLM_MODEL must be set in the environment when "
                "BANK_CROSS_VLM_VERIFY is enabled."
            )
        if not page_txns:
            return page_txns

        from app.bank_prompts.boc import BOC_AR_MANAGER_PROMPT_PREFIX
        from app.services.bank_vlm_hsbc_manager_merge import (
            build_bookkeeper_snapshot,
            merge_manager_into_bookkeeper,
        )

        max_tokens = max(512, int(os.getenv("HSBC_MANAGER_MAX_TOKENS", "4096")))
        desc_max = max(20, int(os.getenv("HSBC_MANAGER_DESC_MAX_CHARS", "100")))
        full_if_n = max(1, int(os.getenv("HSBC_MANAGER_FULL_JSON_MAX_ROWS", "15")))
        tol = float(os.getenv("HSBC_MANAGER_AMOUNT_TOLERANCE", "0.02"))

        tmp_path, page_hash, image_opts = self._boc_write_page_jpeg_for_manager(
            page, page_num
        )
        try:
            snapshot = build_bookkeeper_snapshot(
                page_txns, desc_max=desc_max, full_if_n_rows=full_if_n
            )
            prompt_full = (
                BOC_AR_MANAGER_PROMPT_PREFIX
                + "\n\nBOOKKEEPER_DRAFT_JSON:\n"
                + snapshot
            )
            manager_txns = await self._run_boc_ar_manager_vlm(
                tmp_path,
                page_hash,
                cross_model,
                "BOC-AR-MGR",
                prompt_full,
                company_identity,
                image_opts,
                max_tokens=max_tokens,
            )
            merged, misaligned, per_ok = merge_manager_into_bookkeeper(
                page_txns, manager_txns, amount_tolerance=tol
            )
            page_needs = misaligned or any(not x for x in per_ok)
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = (
                    "needs_review" if page_needs else "verified"
                )
            for i, _row in enumerate(merged):
                bad = misaligned or (i < len(per_ok) and not per_ok[i])
                _row["_ar_manager_status"] = "needs_review" if bad else "verified"
            return merged
        except Exception as mgr_err:
            logger.warning(
                "[BOC-AR-MGR] Page %d failed: %s",
                page_num + 1,
                mgr_err,
                exc_info=True,
            )
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = "needs_review"
            for _row in page_txns:
                _row["_ar_manager_status"] = "error"
            return page_txns
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    async def _bocom_apply_ar_manager_if_enabled(
        self,
        page,
        page_num: int,
        page_txns: List[Dict[str, Any]],
        page_verification_out: Dict[int, str] | None,
        company_identity: Dict[str, Any] | None,
    ) -> List[Dict[str, Any]]:
        """Optional second VLM (BANK_CROSS_VLM_*): BOCOM balance-only merge like HSBC/BOC."""
        cross_on = os.getenv("BANK_CROSS_VLM_VERIFY", "").lower() in (
            "1",
            "true",
            "yes",
        )
        cross_model = os.getenv("BANK_CROSS_VLM_MODEL", "").strip()
        if not cross_on:
            return page_txns
        if not cross_model:
            raise RuntimeError(
                "BANK_CROSS_VLM_MODEL must be set in the environment when "
                "BANK_CROSS_VLM_VERIFY is enabled."
            )
        if not page_txns:
            return page_txns

        from app.bank_prompts.bocom import BOCOM_AR_MANAGER_PROMPT_PREFIX
        from app.services.bank_vlm_hsbc_manager_merge import (
            build_bookkeeper_snapshot,
            merge_manager_into_bookkeeper,
        )

        max_tokens = max(512, int(os.getenv("HSBC_MANAGER_MAX_TOKENS", "4096")))
        desc_max = max(20, int(os.getenv("HSBC_MANAGER_DESC_MAX_CHARS", "100")))
        full_if_n = max(1, int(os.getenv("HSBC_MANAGER_FULL_JSON_MAX_ROWS", "15")))
        tol = float(os.getenv("HSBC_MANAGER_AMOUNT_TOLERANCE", "0.02"))

        tmp_path, page_hash, image_opts = self._bocom_write_page_jpeg_for_manager(
            page, page_num
        )
        try:
            snapshot = build_bookkeeper_snapshot(
                page_txns, desc_max=desc_max, full_if_n_rows=full_if_n
            )
            prompt_full = (
                BOCOM_AR_MANAGER_PROMPT_PREFIX
                + "\n\nBOOKKEEPER_DRAFT_JSON:\n"
                + snapshot
            )
            manager_txns = await self._run_bocom_ar_manager_vlm(
                tmp_path,
                page_hash,
                cross_model,
                "BOCOM-AR-MGR",
                prompt_full,
                company_identity,
                image_opts,
                max_tokens=max_tokens,
            )
            merged, misaligned, per_ok = merge_manager_into_bookkeeper(
                page_txns, manager_txns, amount_tolerance=tol
            )
            page_needs = misaligned or any(not x for x in per_ok)
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = (
                    "needs_review" if page_needs else "verified"
                )
            for i, _row in enumerate(merged):
                bad = misaligned or (i < len(per_ok) and not per_ok[i])
                _row["_ar_manager_status"] = "needs_review" if bad else "verified"
            return merged
        except Exception as mgr_err:
            logger.warning(
                "[BOCOM-AR-MGR] Page %d failed: %s",
                page_num + 1,
                mgr_err,
                exc_info=True,
            )
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = "needs_review"
            for _row in page_txns:
                _row["_ar_manager_status"] = "error"
            return page_txns
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    async def _scb_apply_ar_manager_if_enabled(
        self,
        page,
        page_num: int,
        page_txns: List[Dict[str, Any]],
        page_verification_out: Dict[int, str] | None,
        company_identity: Dict[str, Any] | None,
    ) -> List[Dict[str, Any]]:
        """Optional second VLM (BANK_CROSS_VLM_*): SCB balance-only merge like BEA/BOC."""
        cross_on = os.getenv("BANK_CROSS_VLM_VERIFY", "").lower() in (
            "1",
            "true",
            "yes",
        )
        cross_model = os.getenv("BANK_CROSS_VLM_MODEL", "").strip()
        if not cross_on:
            return page_txns
        if not cross_model:
            raise RuntimeError(
                "BANK_CROSS_VLM_MODEL must be set in the environment when "
                "BANK_CROSS_VLM_VERIFY is enabled."
            )
        if not page_txns:
            return page_txns

        from app.bank_prompts.sc import SCB_AR_MANAGER_PROMPT_PREFIX
        from app.services.bank_vlm_hsbc_manager_merge import (
            build_bookkeeper_snapshot,
            merge_manager_into_bookkeeper,
        )

        max_tokens = max(512, int(os.getenv("HSBC_MANAGER_MAX_TOKENS", "4096")))
        desc_max = max(20, int(os.getenv("HSBC_MANAGER_DESC_MAX_CHARS", "100")))
        full_if_n = max(1, int(os.getenv("HSBC_MANAGER_FULL_JSON_MAX_ROWS", "15")))
        tol = float(os.getenv("HSBC_MANAGER_AMOUNT_TOLERANCE", "0.02"))

        tmp_path, page_hash, image_opts = self._scb_write_page_jpeg_for_manager(
            page, page_num
        )
        try:
            snapshot = build_bookkeeper_snapshot(
                page_txns, desc_max=desc_max, full_if_n_rows=full_if_n
            )
            prompt_full = (
                SCB_AR_MANAGER_PROMPT_PREFIX
                + "\n\nBOOKKEEPER_DRAFT_JSON:\n"
                + snapshot
            )
            manager_txns = await self._run_scb_ar_manager_vlm(
                tmp_path,
                page_hash,
                cross_model,
                "SCB-AR-MGR",
                prompt_full,
                company_identity,
                image_opts,
                max_tokens=max_tokens,
            )
            merged, misaligned, per_ok = merge_manager_into_bookkeeper(
                page_txns, manager_txns, amount_tolerance=tol
            )
            page_needs = misaligned or any(not x for x in per_ok)
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = (
                    "needs_review" if page_needs else "verified"
                )
            for i, _row in enumerate(merged):
                bad = misaligned or (i < len(per_ok) and not per_ok[i])
                _row["_ar_manager_status"] = "needs_review" if bad else "verified"
            return merged
        except Exception as mgr_err:
            logger.warning(
                "[SCB-AR-MGR] Page %d failed: %s",
                page_num + 1,
                mgr_err,
                exc_info=True,
            )
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = "needs_review"
            for _row in page_txns:
                _row["_ar_manager_status"] = "error"
            return page_txns
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    async def _hang_seng_apply_ar_manager_if_enabled(
        self,
        page,
        page_num: int,
        page_txns: List[Dict[str, Any]],
        page_verification_out: Dict[int, str] | None,
        company_identity: Dict[str, Any] | None,
    ) -> List[Dict[str, Any]]:
        """Optional BANK_CROSS_VLM_* pass: balance-only merge (same pattern as BEA/SCB)."""
        cross_on = os.getenv("BANK_CROSS_VLM_VERIFY", "").lower() in (
            "1",
            "true",
            "yes",
        )
        cross_model = os.getenv("BANK_CROSS_VLM_MODEL", "").strip()
        if not cross_on:
            return page_txns
        if not cross_model:
            raise RuntimeError(
                "BANK_CROSS_VLM_MODEL must be set in the environment when "
                "BANK_CROSS_VLM_VERIFY is enabled."
            )
        if not page_txns:
            return page_txns

        from app.bank_prompts.hang_seng import HANG_SENG_AR_MANAGER_PROMPT_PREFIX
        from app.services.bank_vlm_hsbc_manager_merge import (
            build_bookkeeper_snapshot,
            merge_manager_into_bookkeeper,
        )

        max_tokens = max(512, int(os.getenv("HSBC_MANAGER_MAX_TOKENS", "4096")))
        desc_max = max(20, int(os.getenv("HSBC_MANAGER_DESC_MAX_CHARS", "100")))
        full_if_n = max(1, int(os.getenv("HSBC_MANAGER_FULL_JSON_MAX_ROWS", "15")))
        tol = float(os.getenv("HSBC_MANAGER_AMOUNT_TOLERANCE", "0.02"))

        tmp_path, page_hash, image_opts = self._hang_seng_write_page_jpeg_for_manager(
            page, page_num
        )
        try:
            snapshot = build_bookkeeper_snapshot(
                page_txns, desc_max=desc_max, full_if_n_rows=full_if_n
            )
            prompt_full = (
                HANG_SENG_AR_MANAGER_PROMPT_PREFIX
                + "\n\nBOOKKEEPER_DRAFT_JSON:\n"
                + snapshot
            )
            manager_txns = await self._run_hang_seng_ar_manager_vlm(
                tmp_path,
                page_hash,
                cross_model,
                "HANG-SENG-AR-MGR",
                prompt_full,
                company_identity,
                image_opts,
                max_tokens=max_tokens,
            )
            merged, misaligned, per_ok = merge_manager_into_bookkeeper(
                page_txns, manager_txns, amount_tolerance=tol
            )
            page_needs = misaligned or any(not x for x in per_ok)
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = (
                    "needs_review" if page_needs else "verified"
                )
            for i, _row in enumerate(merged):
                bad = misaligned or (i < len(per_ok) and not per_ok[i])
                _row["_ar_manager_status"] = "needs_review" if bad else "verified"
            return merged
        except Exception as mgr_err:
            logger.warning(
                "[HANG-SENG-AR-MGR] Page %d failed: %s",
                page_num + 1,
                mgr_err,
                exc_info=True,
            )
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = "needs_review"
            for _row in page_txns:
                _row["_ar_manager_status"] = "error"
            return page_txns
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    async def _ocbc_apply_ar_manager_if_enabled(
        self,
        page,
        page_num: int,
        page_txns: List[Dict[str, Any]],
        page_verification_out: Dict[int, str] | None,
        company_identity: Dict[str, Any] | None,
    ) -> List[Dict[str, Any]]:
        """Optional BANK_CROSS_VLM_* pass: OCBC balance-only merge (same pattern as BEA/BOC)."""
        cross_on = os.getenv("BANK_CROSS_VLM_VERIFY", "").lower() in (
            "1",
            "true",
            "yes",
        )
        cross_model = os.getenv("BANK_CROSS_VLM_MODEL", "").strip()
        if not cross_on:
            return page_txns
        if not cross_model:
            raise RuntimeError(
                "BANK_CROSS_VLM_MODEL must be set in the environment when "
                "BANK_CROSS_VLM_VERIFY is enabled."
            )
        if not page_txns:
            return page_txns

        from app.bank_prompts.ocbc import OCBC_AR_MANAGER_PROMPT_PREFIX
        from app.services.bank_vlm_hsbc_manager_merge import (
            build_bookkeeper_snapshot,
            merge_manager_into_bookkeeper,
        )

        max_tokens = max(512, int(os.getenv("HSBC_MANAGER_MAX_TOKENS", "4096")))
        desc_max = max(20, int(os.getenv("HSBC_MANAGER_DESC_MAX_CHARS", "100")))
        full_if_n = max(1, int(os.getenv("HSBC_MANAGER_FULL_JSON_MAX_ROWS", "15")))
        tol = float(os.getenv("HSBC_MANAGER_AMOUNT_TOLERANCE", "0.02"))

        tmp_path, page_hash, image_opts = self._ocbc_write_page_jpeg_for_manager(
            page, page_num
        )
        try:
            snapshot = build_bookkeeper_snapshot(
                page_txns, desc_max=desc_max, full_if_n_rows=full_if_n
            )
            prompt_full = (
                OCBC_AR_MANAGER_PROMPT_PREFIX
                + "\n\nBOOKKEEPER_DRAFT_JSON:\n"
                + snapshot
            )
            manager_txns = await self._run_ocbc_ar_manager_vlm(
                tmp_path,
                page_hash,
                cross_model,
                "OCBC-AR-MGR",
                prompt_full,
                company_identity,
                image_opts,
                max_tokens=max_tokens,
            )
            merged, misaligned, per_ok = merge_manager_into_bookkeeper(
                page_txns, manager_txns, amount_tolerance=tol
            )
            page_needs = misaligned or any(not x for x in per_ok)
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = (
                    "needs_review" if page_needs else "verified"
                )
            for i, _row in enumerate(merged):
                bad = misaligned or (i < len(per_ok) and not per_ok[i])
                _row["_ar_manager_status"] = "needs_review" if bad else "verified"
            return merged
        except Exception as mgr_err:
            logger.warning(
                "[OCBC-AR-MGR] Page %d failed: %s",
                page_num + 1,
                mgr_err,
                exc_info=True,
            )
            if page_verification_out is not None:
                page_verification_out[page_num + 1] = "needs_review"
            for _row in page_txns:
                _row["_ar_manager_status"] = "error"
            return page_txns
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    async def _run_vlm_on_chunks(
        self,
        page,                               # fitz.Page — rendered inside this method
        prompt: str,
        page_hash: str,
        vlm_model: str,
        track_name: str,
        company_identity: Dict[str, Any] | None = None,
        max_tokens: int = 8000,
        image_options: dict | None = None,
        chunk_count: int = 3,
        chunk_overlap_ratio: float = 0.08,
        *,
        filter_balance_anchor_rows: bool = True,
        reconcile_mode: str = "delta",
    ) -> List[Dict[str, Any]]:
        """Split a dense PDF page into chunks and run VLM sequentially on each.

        ``filter_balance_anchor_rows`` and ``reconcile_mode`` are forwarded to each
        ``_run_vlm_track`` call (BEA dual-track passes False so B/F opening rows are
        not stripped; OCBC uses reconcile_mode=\"none\").

        Chunking strategy (OpenCV dual-strategy):
          - Render at 300 DPI for higher per-chunk legibility (each chunk is ~1/3
            of the page, so payload stays small even at higher resolution).
          - Strategy A: split at morphological horizontal grid lines when >= 3 found.
          - Strategy B: fixed-height splits (975px @ 300 DPI ≈ 15-20 txn rows) with
            90px overlap when grid lines are absent (e.g. SCB Pages 3, 4).
          - Falls back to equal-height PIL split if cv2/numpy unavailable.
          - Execute VLM tracks sequentially (no asyncio.gather) to avoid API saturation.
          - Merge results in top-to-bottom order; deduplicate using a 4-field key.
          - Higher max_side=1200 per chunk is safe: chunk payload ~80-120KB vs
            ~215KB for a full page at max_side=800.

        The dedup key (date, description[:60], credit_or_debit, balance) handles
        overlap rows that appear in two adjacent chunks.
        """
        import io as _io
        import fitz as _fitz
        from PIL import Image as _PILImage
        import numpy as _np
        import cv2 as _cv2

        # 300 DPI: higher quality per chunk; safe because each chunk is ~1/3 page
        render_dpi = int(os.getenv("BANK_CHUNK_RENDER_DPI", "300"))
        scale = render_dpi / 72.0

        # Render once as RGB — reused for both OpenCV split detection and PIL crop/save
        pix_rgb = page.get_pixmap(
            matrix=_fitz.Matrix(scale, scale),
            colorspace=_fitz.csRGB,
        )
        img_rgb_np = _np.frombuffer(pix_rgb.samples, dtype=_np.uint8).reshape(
            pix_rgb.height, pix_rgb.width, 3
        )
        img_bgr = _cv2.cvtColor(img_rgb_np, _cv2.COLOR_RGB2BGR)

        # Chunk height and overlap scale with DPI so each chunk covers ~15-20 rows
        # regardless of render resolution.  Env vars allow tuning without code changes.
        _chunk_max_h = int(os.getenv(
            "BANK_CHUNK_MAX_HEIGHT_PX",
            str(int(650 * render_dpi / 200)),   # 975px @ 300 DPI
        ))
        _chunk_overlap = int(os.getenv(
            "BANK_CHUNK_OVERLAP_PX",
            str(int(60 * render_dpi / 200)),    # 90px @ 300 DPI
        ))

        try:
            chunk_bounds = BankStatementParser._cv_intelligent_chunk_page(
                img_bgr,
                max_chunk_height_px=_chunk_max_h,
                overlap_px=_chunk_overlap,
            )
            logger.info(
                f"[CHUNK-CV] {len(chunk_bounds)} chunks at {render_dpi} DPI "
                f"(max_h={_chunk_max_h}px, overlap={_chunk_overlap}px)"
            )
        except Exception as _cv_err:
            logger.warning(
                f"[CHUNK-CV] OpenCV chunking failed ({_cv_err}), "
                f"falling back to equal-height split"
            )
            img_h_fb = pix_rgb.height
            strip_h_fb = img_h_fb // chunk_count
            overlap_fb = int(img_h_fb * chunk_overlap_ratio)
            chunk_bounds = [
                (max(0, i * strip_h_fb - overlap_fb),
                 min(img_h_fb, (i + 1) * strip_h_fb + overlap_fb))
                for i in range(chunk_count)
            ]

        full_img = _PILImage.open(_io.BytesIO(pix_rgb.tobytes("png")))
        img_w, img_h = full_img.size
        total_chunks = len(chunk_bounds)

        all_txns: List[Dict[str, Any]] = []
        seen_keys: set = set()
        # Higher max_side is safe per chunk: smaller content area = smaller payload
        effective_img_opts = image_options or {"max_side": 1200, "format": "JPEG", "quality": 85}

        for idx, (top, bottom) in enumerate(chunk_bounds):
            chunk_img = full_img.crop((0, top, img_w, bottom))

            tmp_chunk = tempfile.NamedTemporaryFile(delete=False, suffix=".jpg")
            tmp_chunk.close()
            try:
                chunk_img.convert("RGB").save(tmp_chunk.name, format="JPEG", quality=85)
                chunk_hash = f"{page_hash}:c{idx}of{total_chunks}"
                chunk_track = f"{track_name}-CHUNK{idx + 1}/{total_chunks}"
                logger.info(
                    f"[CHUNK] {chunk_track} — rows {top}–{bottom}px "
                    f"of {img_h}px total"
                )
                chunk_txns = await self._run_vlm_track(
                    tmp_chunk.name,
                    prompt,
                    chunk_hash,
                    vlm_model,
                    chunk_track,
                    company_identity,
                    max_tokens=max_tokens,
                    image_options=effective_img_opts,
                    filter_balance_anchor_rows=filter_balance_anchor_rows,
                    reconcile_mode=reconcile_mode,
                )
            finally:
                if os.path.exists(tmp_chunk.name):
                    os.remove(tmp_chunk.name)

            # Cross-chunk deduplication: keep the first occurrence of each unique row.
            added = 0
            for txn in chunk_txns:
                dedup_key = (
                    str(txn.get("日期") or txn.get("date") or "").strip(),
                    str(txn.get("備註") or txn.get("description") or "")[:60].strip(),
                    str(
                        txn.get("存入") or txn.get("credit") or
                        txn.get("提取") or txn.get("debit") or ""
                    ).strip(),
                    str(txn.get("原幣結餘") or txn.get("balance") or "").strip(),
                )
                if dedup_key not in seen_keys:
                    seen_keys.add(dedup_key)
                    all_txns.append(txn)
                    added += 1

            logger.info(
                f"[CHUNK] {chunk_track} — {len(chunk_txns)} txns extracted, "
                f"{added} new after cross-chunk dedup"
            )

        logger.info(
            f"[CHUNK] {track_name} — merged total: {len(all_txns)} transactions "
            f"from {total_chunks} chunks"
        )
        return all_txns

    async def _identify_bank_from_image(self, file_path: str) -> str:
        """Run a lightweight VLM call on page 1 to identify the bank for image-based PDFs.

        Called only when text extraction yields no usable content (0-char image PDF)
        and the bank cannot be determined by pattern-matching.  Uses a tiny prompt
        (max_tokens=64) so the call is fast and cheap.

        Returns a recognised bank_id ('SCB', 'BOC', 'OCBC', …) or 'UNKNOWN'.
        """
        from app.bank_prompts import BANK_KEYWORDS
        from app.ocr.runtime import BANK_VLM_MODEL, ocr_service as _ocr_service
        import fitz

        try:
            doc = fitz.open(file_path)
            if not doc:
                return 'UNKNOWN'

            page = doc[0]
            pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
            page_hash = hashlib.sha256(pix.samples).hexdigest()
            prompt_hash = hashlib.sha256(
                self._BANK_IDENTIFICATION_PROMPT.encode('utf-8')
            ).hexdigest()[:16]
            cache_key = f"bank-id:{page_hash}:{BANK_VLM_MODEL}:{prompt_hash}"

            page_text = self._get_cached_ocr_text(cache_key)
            if page_text is None:
                logger.info("[BANK-ID] Cache miss — running VLM bank identification on page 1...")
                tmp_img = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                tmp_img_path = tmp_img.name
                tmp_img.close()
                pix.save(tmp_img_path)
                try:
                    ocr_result = await _ocr_service.recognize(
                        tmp_img_path,
                        provider_name=BANK_VLM_MODEL,
                        model=BANK_VLM_MODEL,
                        prompt_override=self._BANK_IDENTIFICATION_PROMPT,
                        ocr_options={
                            "max_tokens": 64,
                            "temperature": 0.0,
                            "enable_thinking": False,
                        },
                        image_options={"max_side": 1200, "format": "JPEG", "quality": 85},
                    )
                    page_text = (ocr_result.text if hasattr(ocr_result, 'text') else '') or ''
                    self._set_cached_ocr_text(cache_key, page_text)
                finally:
                    if os.path.exists(tmp_img_path):
                        # Windows can keep this temp file locked briefly when OCR
                        # gets cancelled/interrupted (reload/Ctrl+C). Retry cleanup
                        # to avoid pre-pass hard-failing on transient WinError 32.
                        for _attempt in range(5):
                            try:
                                os.remove(tmp_img_path)
                                break
                            except FileNotFoundError:
                                break
                            except PermissionError:
                                if _attempt < 4:
                                    await asyncio.sleep(0.1)
                                else:
                                    logger.warning(
                                        "[BANK-ID] Temp image still locked during cleanup: %s",
                                        tmp_img_path,
                                    )
                            except Exception:
                                logger.warning(
                                    "[BANK-ID] Failed to clean up temp image: %s",
                                    tmp_img_path,
                                    exc_info=True,
                                )
                                break

            logger.info(f"[BANK-ID] VLM response: {page_text[:200]!r}")
            parsed = self._extract_json_from_vlm_output(page_text)
            if isinstance(parsed, dict):
                bank_id = str(parsed.get('bank_id', 'UNKNOWN')).strip().upper()
                known_banks = set(BANK_KEYWORDS.keys()) | {'HSBC', 'HANG_SENG', 'DBS', 'BEA'}
                if bank_id in known_banks:
                    logger.info(f"[BANK-ID] Identified bank: {bank_id}")
                    return bank_id
                logger.info(f"[BANK-ID] Unrecognised bank_id {bank_id!r} — staying UNKNOWN")

        except Exception as e:
            logger.warning(f"[BANK-ID] Bank identification pre-pass failed: {e}", exc_info=True)

        return 'UNKNOWN'

    async def _parse_with_ocr_fallback(
        self,
        file_path: str,
        bank_type: str,
        company_identity: Dict[str, Any] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        page_verification_out: Dict[int, str] | None = None,
    ) -> List[Dict]:
        """Use dual-track VLM to extract ALL transactions from ALL pages.

        Known banks (BOC, OCBC, …): run PRIMARY (bank-specific) + FALLBACK (DEFAULT) in parallel,
        then arbitrate by valid transaction count + average confidence.
        Unknown banks: run DEFAULT prompt only (no wasted VLM call).
        """
        logger.info(f"[BANK] Dual-track VLM pipeline starting for bank_type={bank_type}")

        try:
            from app.ocr.runtime import BANK_VLM_MODEL, ocr_service as _ocr_service
            from app.bank_prompts import BANK_PROMPT_DATABASE

            if not _ocr_service:
                logger.error("[BANK] OCR service not available")
                return []

            import fitz
            doc = fitz.open(file_path)
            page_count = len(doc)
            parallel_pages = int(os.getenv("BANK_PARALLEL_PAGES", "1") or "1")
            parallel_pages = max(1, min(parallel_pages, 8))
            semaphore = asyncio.Semaphore(parallel_pages)
            completed_pages = 0
            progress_lock = asyncio.Lock()

            specific_prompt = BANK_PROMPT_DATABASE.get(bank_type)  # None if UNKNOWN
            default_prompt = BANK_PROMPT_DATABASE["DEFAULT"]
            mode = "dual-track" if specific_prompt else "default-only"
            # OCBC: skip blind per-page delta overwrite; gated policy runs after assemble.
            _reconcile_mode = "none" if bank_type == "OCBC" else "delta"

            # ── v2.1 adaptive-density feature flags ──────────────────────────────
            _density_classify   = os.getenv("BANK_DENSITY_CLASSIFY", "true").lower() == "true"
            _adaptive_density   = os.getenv("BANK_ADAPTIVE_DENSITY", "false").lower() == "true"
            _dense_chunking     = os.getenv("BANK_DENSE_CHUNKING", "false").lower() == "true"
            _dense_max_side     = int(os.getenv("BANK_DENSE_MAX_SIDE", "900"))
            _sparse_max_side    = int(os.getenv("BANK_SPARSE_MAX_SIDE", "800"))
            _dense_chunk_count  = max(2, min(int(os.getenv("BANK_DENSE_CHUNK_COUNT", "3")), 5))
            _chunk_overlap      = float(os.getenv("BANK_DENSE_CHUNK_OVERLAP", "0.08"))
            _chunk_min_est_rows = max(1, int(os.getenv("BANK_CHUNK_MIN_EST_ROWS", "10")))
            # Clamp to safe range regardless of env value.
            _dense_max_side  = max(800, min(_dense_max_side, 1100))
            _sparse_max_side = max(700, min(_sparse_max_side, 900))

            # ── Bank-specific chunking allowlist ─────────────────────────────────
            # Banks listed here use OpenCV chunking via the allowlist path, which is
            # independent of BANK_ADAPTIVE_DENSITY.  All other banks are unaffected.
            _chunk_banks = {
                b.strip().upper()
                for b in os.getenv("BANK_CHUNK_BANKS", "").split(",")
                if b.strip()
            }

            logger.info(
                f"[BANK] pages={page_count}, parallel={parallel_pages}, "
                f"vlm_model={BANK_VLM_MODEL}, mode={mode} | "
                f"density_classify={_density_classify}, adaptive={_adaptive_density}, "
                f"chunking={_dense_chunking}, chunk_banks={_chunk_banks or 'none'}, "
                f"chunk_min_est_rows={_chunk_min_est_rows}"
            )

            _cross_verify = os.getenv("BANK_CROSS_VLM_VERIFY", "").lower() in (
                "1",
                "true",
                "yes",
            )
            _cross_model = os.getenv("BANK_CROSS_VLM_MODEL", "").strip()
            if _cross_verify:
                if not _cross_model:
                    raise RuntimeError(
                        "BANK_CROSS_VLM_MODEL must be set in the environment when "
                        "BANK_CROSS_VLM_VERIFY is enabled."
                    )
                logger.info(
                    "[BANK][CROSS-VLM] enabled cross_model=%s "
                    "(full-page balance/totals checker; chunked and non-chunked)",
                    _cross_model,
                )

            page_density_map: Dict[int, Dict[str, Any]] = {}

            async def process_page(page_num: int) -> tuple[int, List[Dict[str, Any]]]:
                nonlocal completed_pages
                async with semaphore:
                    page_v_status: str | None = None
                    try:
                        page = doc[page_num]
                        logger.info(f"[BANK] Processing page {page_num + 1}/{page_count}")

                        if bank_type == "BEA":
                            if BankStatementParser._bea_is_cover_like_portfolio_page(
                                page.get_text() or ""
                            ):
                                logger.info(
                                    "[BEA][BANK] Page %d — portfolio/cover, skip VLM",
                                    page_num + 1,
                                )
                                async with progress_lock:
                                    completed_pages += 1
                                    progress_percent = min(
                                        95,
                                        20 + int(
                                            (completed_pages / max(page_count, 1)) * 70
                                        ),
                                    )
                                    self._emit_progress(
                                        progress_callback,
                                        percent=progress_percent,
                                        label=(
                                            f"VLM 處理中（第 {completed_pages}/{page_count} 頁完成）"
                                        ),
                                        page_current=completed_pages,
                                        page_total=page_count,
                                    )
                                return page_num, []

                        if bank_type == "HANG_SENG":
                            if BankStatementParser._hang_seng_is_cover_like_portfolio_page(
                                page.get_text() or ""
                            ):
                                logger.info(
                                    "[HANG_SENG][BANK] Page %d — portfolio/cover, skip VLM",
                                    page_num + 1,
                                )
                                async with progress_lock:
                                    completed_pages += 1
                                    progress_percent = min(
                                        95,
                                        20 + int(
                                            (completed_pages / max(page_count, 1)) * 70
                                        ),
                                    )
                                    self._emit_progress(
                                        progress_callback,
                                        percent=progress_percent,
                                        label=(
                                            f"VLM 處理中（第 {completed_pages}/{page_count} 頁完成）"
                                        ),
                                        page_current=completed_pages,
                                        page_total=page_count,
                                    )
                                return page_num, []

                        if bank_type == "OCBC":
                            if BankStatementParser._ocbc_is_cover_like_portfolio_page(
                                page.get_text() or ""
                            ):
                                logger.info(
                                    "[OCBC][BANK] Page %d — summary/cover, skip VLM",
                                    page_num + 1,
                                )
                                async with progress_lock:
                                    completed_pages += 1
                                    progress_percent = min(
                                        95,
                                        20 + int(
                                            (completed_pages / max(page_count, 1)) * 70
                                        ),
                                    )
                                    self._emit_progress(
                                        progress_callback,
                                        percent=progress_percent,
                                        label=(
                                            f"VLM 處理中（第 {completed_pages}/{page_count} 頁完成）"
                                        ),
                                        page_current=completed_pages,
                                        page_total=page_count,
                                    )
                                return page_num, []

                        # ── Phase 1: Density classification (always logs; behavior change
                        #    only when _adaptive_density=True) ───────────────────────────
                        density_info = {"level": "UNKNOWN", "confidence": 0.0,
                                        "estimated_rows": 0, "method": "skipped"}
                        if _density_classify:
                            density_info = self._classify_page_density(page)
                            logger.info(
                                f"[DENSITY] Page {page_num + 1}: "
                                f"level={density_info['level']}, "
                                f"conf={density_info['confidence']:.2f}, "
                                f"est_rows={density_info['estimated_rows']}, "
                                f"method={density_info['method']}"
                            )
                        page_density_map[page_num] = density_info

                        # ── Phase 2: Select image options based on density ─────────────
                        density_level = density_info["level"]
                        density_conf  = density_info["confidence"]
                        _gate = BankStatementParser._DENSITY_CONFIDENCE_GATE

                        if _adaptive_density and density_conf >= _gate:
                            if density_level == "DENSE":
                                page_image_opts = {
                                    "max_side": _dense_max_side,
                                    "format": "JPEG",
                                    "quality": 85,
                                }
                            elif density_level == "SPARSE":
                                page_image_opts = {
                                    "max_side": _sparse_max_side,
                                    "format": "JPEG",
                                    "quality": 85,
                                }
                            else:
                                page_image_opts = {"max_side": 800, "format": "JPEG", "quality": 85}
                        else:
                            # Baseline: identical to pre-v2.1 behavior.
                            page_image_opts = {"max_side": 800, "format": "JPEG", "quality": 85}

                        # ── Phase 3: Route pages to chunker ──────────────────────────
                        # Two independent paths — both require BANK_DENSE_CHUNKING=true:
                        #
                        # Path 1 (allowlist): bank_type in BANK_CHUNK_BANKS.
                        #   Does NOT require BANK_ADAPTIVE_DENSITY=true.
                        #   Fires whenever the page is NOT confirmed SPARSE.
                        #   Used for SCB-only test; BOC/HSBC/others are unaffected.
                        #
                        # Path 2 (global adaptive): any bank when BANK_ADAPTIVE_DENSITY=true
                        #   AND density classifier returns DENSE with sufficient confidence.
                        #   Currently disabled (BANK_ADAPTIVE_DENSITY=false).
                        _in_allowlist = bool(_chunk_banks) and bank_type in _chunk_banks
                        _definite_sparse = (density_level == "SPARSE" and density_conf >= _gate)
                        _est_rows = int(density_info.get("estimated_rows") or 0)
                        _allowlist_chunk_ok = (
                            _in_allowlist
                            and not _definite_sparse
                            and _est_rows >= _chunk_min_est_rows
                        )
                        use_chunking = (
                            _dense_chunking
                            and (
                                _allowlist_chunk_ok
                                or (_adaptive_density and density_level == "DENSE"
                                    and density_conf >= _gate)
                            )
                        )

                        pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
                        page_hash = hashlib.sha256(pix.samples).hexdigest()

                        # Write image once; both tracks share the same file.
                        tmp_img = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                        tmp_img_path = tmp_img.name
                        tmp_img.close()
                        pix.save(tmp_img_path)

                        try:
                            winning_prompt = default_prompt
                            if specific_prompt:
                                # Sequential dual-track: run PRIMARY first; only run FALLBACK
                                # if PRIMARY yields no transactions.  Running both in parallel
                                # doubles the concurrent API load per page and was causing the
                                # VLM server to push both requests past the 180 s read timeout
                                # on dense transaction pages (e.g. SCB page 3).
                                if use_chunking:
                                    logger.info(
                                        f"[DENSITY] Page {page_num + 1} routed to OpenCV chunker "
                                        f"(allowlist={_in_allowlist}, density={density_level})"
                                    )
                                    primary_txns = await self._run_vlm_on_chunks(
                                        page,
                                        specific_prompt,
                                        page_hash,
                                        BANK_VLM_MODEL,
                                        "PRIMARY",
                                        company_identity,
                                        chunk_count=_dense_chunk_count,
                                        chunk_overlap_ratio=_chunk_overlap,
                                        image_options=None,
                                        filter_balance_anchor_rows=(
                                            bank_type not in ("BEA", "SCB")
                                        ),
                                        reconcile_mode=_reconcile_mode,
                                    )
                                else:
                                    primary_txns = await self._run_vlm_track(
                                        tmp_img_path, specific_prompt, page_hash,
                                        BANK_VLM_MODEL, "PRIMARY", company_identity,
                                        image_options=page_image_opts,
                                        filter_balance_anchor_rows=(
                                            bank_type not in ("BEA", "SCB")
                                        ),
                                        reconcile_mode=_reconcile_mode,
                                    )
                                primary_score = self._score_vlm_result(primary_txns)

                                if primary_score > 0:
                                    page_transactions = primary_txns
                                    fallback_txns: List[Dict[str, Any]] = []
                                    fallback_score = 0.0
                                    selected = "PRIMARY"
                                    winning_prompt = specific_prompt
                                else:
                                    # FALLBACK also respects density-based image options.
                                    fallback_txns = await self._run_vlm_track(
                                        tmp_img_path, default_prompt, page_hash,
                                        BANK_VLM_MODEL, "FALLBACK", company_identity,
                                        image_options=page_image_opts,
                                        filter_balance_anchor_rows=(
                                            bank_type not in ("BEA", "SCB")
                                        ),
                                        reconcile_mode=_reconcile_mode,
                                    )
                                    fallback_score = self._score_vlm_result(fallback_txns)
                                    if fallback_score > 0:
                                        page_transactions = fallback_txns
                                        selected = "FALLBACK"
                                        winning_prompt = default_prompt
                                    else:
                                        page_transactions = []
                                        selected = "NONE"
                                        winning_prompt = default_prompt

                                logger.info(
                                    f"[ARBITRATE] Page {page_num + 1} — "
                                    f"PRIMARY: {len(primary_txns)} txns (score {primary_score:.2f}) | "
                                    f"FALLBACK: {len(fallback_txns)} txns (score {fallback_score:.2f}) "
                                    f"→ Selected: {selected}"
                                )
                            else:
                                # Unknown bank — DEFAULT prompt only; still respects
                                # density-based image options.
                                page_transactions = await self._run_vlm_track(
                                    tmp_img_path, default_prompt, page_hash,
                                    BANK_VLM_MODEL, "DEFAULT", company_identity,
                                    image_options=page_image_opts,
                                    reconcile_mode=_reconcile_mode,
                                )
                                winning_prompt = default_prompt
                                logger.info(
                                    f"[BANK] Page {page_num + 1} (UNKNOWN) — "
                                    f"DEFAULT: {len(page_transactions)} txns"
                                )

                            if bank_type == "BEA" and page_transactions:
                                page_transactions = (
                                    await self._bea_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        page_transactions,
                                        page_verification_out,
                                        company_identity,
                                    )
                                )
                            if bank_type == "BOC" and page_transactions:
                                page_transactions = (
                                    await self._boc_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        page_transactions,
                                        page_verification_out,
                                        company_identity,
                                    )
                                )
                            if bank_type == "SCB" and page_transactions:
                                page_transactions = (
                                    await self._scb_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        page_transactions,
                                        page_verification_out,
                                        company_identity,
                                    )
                                )
                            if bank_type == "HANG_SENG" and page_transactions:
                                page_transactions = (
                                    await self._hang_seng_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        page_transactions,
                                        page_verification_out,
                                        company_identity,
                                    )
                                )
                            if bank_type == "OCBC" and page_transactions:
                                page_transactions = (
                                    await self._ocbc_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        page_transactions,
                                        page_verification_out,
                                        company_identity,
                                    )
                                )

                            # Cross-VLM model B: full-page balance/totals checker (same tmp image).
                            if _cross_verify:
                                try:
                                    from app.services.bank_vlm_balance_check import (
                                        compare_balance_checker,
                                        sum_primary_deposits_withdrawals,
                                    )

                                    checker = await self._run_balance_checker_vlm(
                                        tmp_img_path,
                                        page_hash,
                                        _cross_model,
                                        "CROSS-B-CHECK",
                                        image_options=page_image_opts,
                                    )
                                    sum_dep, sum_wdr = sum_primary_deposits_withdrawals(
                                        page_transactions
                                    )
                                    page_v_status, chk_reason = compare_balance_checker(
                                        checker,
                                        sum_dep,
                                        sum_wdr,
                                    )
                                    logger.info(
                                        "[BANK][CROSS-VLM][CHECK] Page %d: %s",
                                        page_num + 1,
                                        chk_reason,
                                    )
                                except Exception as cross_err:
                                    logger.warning(
                                        "[BANK][CROSS-VLM] Page %d failed: %s",
                                        page_num + 1,
                                        cross_err,
                                        exc_info=True,
                                    )
                                    page_v_status = "needs_review"
                                if page_verification_out is not None and page_v_status:
                                    page_verification_out[page_num + 1] = page_v_status
                        finally:
                            if os.path.exists(tmp_img_path):
                                os.remove(tmp_img_path)

                        # Fill missing account_type using embedded PDF text layer as a hint.
                        # For image-based PDFs this will be empty; VLM-provided account_type
                        # is already in the transactions so this is just a safety fallback.
                        inferred_account_type = self._infer_account_type_from_text(
                            page.get_text() or ""
                        )
                        if inferred_account_type:
                            for txn in page_transactions:
                                existing = str(
                                    txn.get("賬戶類型")
                                    or txn.get("帳戶類型")
                                    or txn.get("账户类型")
                                    or txn.get("account_type")
                                    or txn.get("account_name")
                                    or ""
                                ).strip()
                                if not existing:
                                    txn["賬戶類型"] = inferred_account_type
                                    txn["account_type"] = inferred_account_type

                        # Tag each transaction with its source page number (1-indexed)
                        for txn in page_transactions:
                            txn['_page'] = page_num + 1

                        if page_transactions:
                            logger.info(
                                f"✅ Extracted {len(page_transactions)} transactions from page {page_num + 1}"
                            )
                        else:
                            logger.warning(f"⚠️ No transactions extracted from page {page_num + 1}")

                        async with progress_lock:
                            completed_pages += 1
                            progress_percent = min(95, 20 + int((completed_pages / max(page_count, 1)) * 70))
                            _prog: dict[str, Any] = {
                                "percent": progress_percent,
                                "label": f"VLM 處理中（第 {completed_pages}/{page_count} 頁完成）",
                                "page_current": completed_pages,
                                "page_total": page_count,
                            }
                            if page_v_status is not None:
                                _prog["page_verification"] = {
                                    str(page_num + 1): page_v_status,
                                }
                            self._emit_progress(progress_callback, **_prog)

                        return page_num, page_transactions

                    except Exception as page_err:
                        logger.error(
                            f"[BANK] process_page({page_num + 1}) raised exception: {page_err}",
                            exc_info=True,
                        )
                        async with progress_lock:
                            completed_pages += 1
                        return page_num, []

            tasks = [process_page(page_num) for page_num in range(page_count)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Build a per-page result index so we can retry failures.
            page_results: Dict[int, List[Dict[str, Any]]] = {}
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"[BANK] Page task failed: {result}", exc_info=True)
                    continue
                page_num_r, page_txns_r = result
                page_results[page_num_r] = page_txns_r

            # ── Round 2: retry pages with 0 transactions OR arithmetic violations ──
            # Pages with 0 transactions are obvious failures.
            # Pages whose winning track has arithmetic violations likely contain
            # hallucinated data (e.g. fabricated balances that don't add up).
            # Retrying with a higher token limit gives the VLM a fresh chance with
            # the same (now-improved) prompts to produce correct output.
            zero_pages_raw = {pn for pn in range(page_count) if not page_results.get(pn)}
            if bank_type in _chunk_banks:
                # SC-only fast/stable guard: skip costly R2 on obvious sparse non-txn pages.
                # Dense transaction pages still retry as normal.
                sparse_zero_pages = {
                    pn for pn in zero_pages_raw
                    if (page_density_map.get(pn, {}).get("level") == "SPARSE")
                    and int(page_density_map.get(pn, {}).get("estimated_rows") or 0) < _chunk_min_est_rows
                }
                if sparse_zero_pages:
                    logger.info(
                        f"[BANK][R2] Skipping sparse zero-page retries (SC allowlist): "
                        f"pages {[p + 1 for p in sorted(sparse_zero_pages)]}"
                    )
                zero_pages = zero_pages_raw - sparse_zero_pages
            else:
                zero_pages = zero_pages_raw
            violation_pages = {
                pn for pn in range(page_count)
                if page_results.get(pn)
                and self._count_arith_violations(page_results[pn]) > 0
            }
            if bank_type in _chunk_banks and violation_pages:
                # Chunked pages often show balance discontinuities at chunk boundaries
                # that look like arithmetic violations but are NOT hallucinations —
                # they are simply the overlap region where consecutive chunks share
                # a few rows.  Retrying those pages full-page at 800px risks 180 s
                # timeouts while the R1 chunked result is already good.
                # Skip R2 for any chunked page whose R1 winning score is high enough
                # to be trusted (threshold = 40.0 ≈ >= 5 well-formed transactions).
                _R2_SKIP_SCORE = 40.0
                high_score_viol = {
                    pn for pn in violation_pages
                    if self._score_vlm_result(page_results.get(pn, [])) >= _R2_SKIP_SCORE
                }
                if high_score_viol:
                    logger.info(
                        f"[BANK][R2] Skipping high-score violation pages (SC allowlist, "
                        f"score≥{_R2_SKIP_SCORE}): pages {[p + 1 for p in sorted(high_score_viol)]}"
                    )
                violation_pages = violation_pages - high_score_viol
            if violation_pages:
                logger.info(
                    f"[BANK][R2] {len(violation_pages)} page(s) have arithmetic violations "
                    f"in winning result — will also retry: pages {[p + 1 for p in sorted(violation_pages)]}"
                )
            failed_page_nums = sorted(zero_pages | violation_pages)
            if failed_page_nums:
                _R2_MAX_TOKENS = _bank_vlm_r2_max_tokens()
                logger.info(
                    f"[BANK][R2] {len(failed_page_nums)} page(s) need R2 retry — "
                    f"max_tokens={_R2_MAX_TOKENS}: pages {[p + 1 for p in failed_page_nums]}"
                )

                async def retry_page(page_num: int) -> tuple[int, List[Dict[str, Any]]]:
                    async with semaphore:
                        try:
                            page = doc[page_num]
                            if bank_type == "BEA":
                                if BankStatementParser._bea_is_cover_like_portfolio_page(
                                    page.get_text() or ""
                                ):
                                    return page_num, []
                            if bank_type == "HANG_SENG":
                                if BankStatementParser._hang_seng_is_cover_like_portfolio_page(
                                    page.get_text() or ""
                                ):
                                    return page_num, []
                            if bank_type == "OCBC":
                                if BankStatementParser._ocbc_is_cover_like_portfolio_page(
                                    page.get_text() or ""
                                ):
                                    return page_num, []

                            pix = page.get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72))
                            r2_hash = hashlib.sha256(pix.samples).hexdigest()

                            use_r2_chunking = (
                                bank_type in _chunk_banks and _dense_chunking
                            )
                            if use_r2_chunking:
                                logger.info(
                                    f"[BANK][R2] Page {page_num + 1} (allowlist) — "
                                    "using chunked R2 to avoid full-page timeouts"
                                )
                                r2_primary = await self._run_vlm_on_chunks(
                                    page, specific_prompt or default_prompt, r2_hash,
                                    BANK_VLM_MODEL, "R2-PRIMARY", company_identity,
                                    _R2_MAX_TOKENS, image_options=None,
                                    chunk_count=_dense_chunk_count,
                                    chunk_overlap_ratio=_chunk_overlap,
                                    filter_balance_anchor_rows=(
                                        bank_type not in ("BEA", "SCB")
                                    ),
                                    reconcile_mode=_reconcile_mode,
                                )
                                r2_ps = self._score_vlm_result(r2_primary)
                                if r2_ps > 0:
                                    r2_txns, r2_sel = r2_primary, "R2-PRIMARY"
                                    r2_fallback: List[Dict[str, Any]] = []
                                    r2_fs = 0.0
                                else:
                                    r2_fallback = await self._run_vlm_on_chunks(
                                        page, default_prompt, r2_hash,
                                        BANK_VLM_MODEL, "R2-FALLBACK", company_identity,
                                        _R2_MAX_TOKENS, image_options=None,
                                        chunk_count=_dense_chunk_count,
                                        chunk_overlap_ratio=_chunk_overlap,
                                        filter_balance_anchor_rows=(
                                            bank_type not in ("BEA", "SCB")
                                        ),
                                        reconcile_mode=_reconcile_mode,
                                    )
                                    r2_fs = self._score_vlm_result(r2_fallback)
                                    if r2_fs > 0:
                                        r2_txns, r2_sel = r2_fallback, "R2-FALLBACK"
                                    else:
                                        r2_txns, r2_sel = [], "R2-NONE"
                                logger.info(
                                    f"[ARBITRATE-R2] Page {page_num + 1} (chunked) — "
                                    f"R2-PRIMARY: {len(r2_primary)} txns (score {r2_ps:.2f}) | "
                                    f"R2-FALLBACK: {len(r2_fallback)} txns (score {r2_fs:.2f}) → {r2_sel}"
                                )
                                if bank_type == "BEA" and r2_txns:
                                    r2_txns = await self._bea_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )
                                if bank_type == "BOC" and r2_txns:
                                    r2_txns = await self._boc_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )
                                if bank_type == "SCB" and r2_txns:
                                    r2_txns = await self._scb_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )
                                if bank_type == "HANG_SENG" and r2_txns:
                                    r2_txns = await self._hang_seng_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )
                                if bank_type == "OCBC" and r2_txns:
                                    r2_txns = await self._ocbc_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )
                                for txn in r2_txns:
                                    txn['_page'] = page_num + 1
                                if r2_txns:
                                    logger.info(
                                        f"✅ [R2] Extracted {len(r2_txns)} transactions from page {page_num + 1}"
                                    )
                                else:
                                    logger.warning(
                                        f"⚠️ [R2] Still no transactions from page {page_num + 1}"
                                    )
                                return page_num, r2_txns

                            if _density_classify:
                                r2_density = self._classify_page_density(page)
                                r2_conf = r2_density["confidence"]
                                _gate = BankStatementParser._DENSITY_CONFIDENCE_GATE
                                if _adaptive_density and r2_conf >= _gate and r2_density["level"] == "DENSE":
                                    r2_image_opts = {"max_side": _dense_max_side, "format": "JPEG", "quality": 85}
                                elif _adaptive_density and r2_conf >= _gate and r2_density["level"] == "SPARSE":
                                    r2_image_opts = {"max_side": _sparse_max_side, "format": "JPEG", "quality": 85}
                                else:
                                    r2_image_opts = {"max_side": 800, "format": "JPEG", "quality": 85}
                            else:
                                r2_image_opts = {"max_side": 800, "format": "JPEG", "quality": 85}

                            tmp_r2 = tempfile.NamedTemporaryFile(delete=False, suffix='.png')
                            tmp_r2_path = tmp_r2.name
                            tmp_r2.close()
                            pix.save(tmp_r2_path)

                            try:
                                if specific_prompt:
                                    r2_primary = await self._run_vlm_track(
                                        tmp_r2_path, specific_prompt, r2_hash,
                                        BANK_VLM_MODEL, "R2-PRIMARY", company_identity,
                                        _R2_MAX_TOKENS,
                                        image_options=r2_image_opts,
                                        filter_balance_anchor_rows=(
                                            bank_type not in ("BEA", "SCB")
                                        ),
                                        reconcile_mode=_reconcile_mode,
                                    )
                                    r2_ps = self._score_vlm_result(r2_primary)

                                    if r2_ps > 0:
                                        r2_txns, r2_sel = r2_primary, "R2-PRIMARY"
                                        r2_fallback = []
                                        r2_fs = 0.0
                                    else:
                                        r2_fallback = await self._run_vlm_track(
                                            tmp_r2_path, default_prompt, r2_hash,
                                            BANK_VLM_MODEL, "R2-FALLBACK", company_identity,
                                            _R2_MAX_TOKENS,
                                            image_options=r2_image_opts,
                                            filter_balance_anchor_rows=(
                                                bank_type not in ("BEA", "SCB")
                                            ),
                                            reconcile_mode=_reconcile_mode,
                                        )
                                        r2_fs = self._score_vlm_result(r2_fallback)
                                        if r2_fs > 0:
                                            r2_txns, r2_sel = r2_fallback, "R2-FALLBACK"
                                        else:
                                            r2_txns, r2_sel = [], "R2-NONE"

                                    logger.info(
                                        f"[ARBITRATE-R2] Page {page_num + 1} — "
                                        f"R2-PRIMARY: {len(r2_primary)} txns (score {r2_ps:.2f}) | "
                                        f"R2-FALLBACK: {len(r2_fallback)} txns (score {r2_fs:.2f}) "
                                        f"→ {r2_sel}"
                                    )
                                else:
                                    r2_txns = await self._run_vlm_track(
                                        tmp_r2_path, default_prompt, r2_hash,
                                        BANK_VLM_MODEL, "R2-DEFAULT", company_identity,
                                        _R2_MAX_TOKENS,
                                        image_options=r2_image_opts,
                                        reconcile_mode=_reconcile_mode,
                                    )
                                    logger.info(
                                        f"[BANK][R2] Page {page_num + 1} (UNKNOWN) — "
                                        f"R2-DEFAULT: {len(r2_txns)} txns"
                                    )

                                if bank_type == "BEA" and r2_txns:
                                    r2_txns = await self._bea_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )
                                if bank_type == "BOC" and r2_txns:
                                    r2_txns = await self._boc_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )
                                if bank_type == "SCB" and r2_txns:
                                    r2_txns = await self._scb_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )
                                if bank_type == "HANG_SENG" and r2_txns:
                                    r2_txns = await self._hang_seng_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )
                                if bank_type == "OCBC" and r2_txns:
                                    r2_txns = await self._ocbc_apply_ar_manager_if_enabled(
                                        page,
                                        page_num,
                                        r2_txns,
                                        page_verification_out,
                                        company_identity,
                                    )

                                for txn in r2_txns:
                                    txn['_page'] = page_num + 1

                                if r2_txns:
                                    logger.info(
                                        f"✅ [R2] Extracted {len(r2_txns)} transactions from page {page_num + 1}"
                                    )
                                else:
                                    logger.warning(
                                        f"⚠️ [R2] Still no transactions from page {page_num + 1}"
                                    )
                                return page_num, r2_txns
                            finally:
                                if os.path.exists(tmp_r2_path):
                                    os.remove(tmp_r2_path)
                        except Exception as r2_err:
                            logger.error(
                                f"[BANK][R2] retry_page({page_num + 1}) failed: {r2_err}",
                                exc_info=True,
                            )
                            return page_num, []

                retry_results = await asyncio.gather(
                    *[retry_page(pn) for pn in failed_page_nums],
                    return_exceptions=True,
                )
                for rr in retry_results:
                    if isinstance(rr, Exception):
                        continue
                    rr_page_num, rr_txns = rr
                    if not rr_txns:
                        continue
                    existing = page_results.get(rr_page_num)
                    if not existing:
                        # Zero-transaction retry — accept any non-empty result.
                        page_results[rr_page_num] = rr_txns
                        if page_verification_out is not None:
                            page_verification_out[rr_page_num + 1] = "needs_review"
                    else:
                        # Violation-based retry — only replace if R2 scores strictly
                        # better than the R1 result to avoid regressing good pages.
                        r1_score = self._score_vlm_result(existing)
                        r2_score = self._score_vlm_result(rr_txns)
                        if r2_score > r1_score:
                            page_results[rr_page_num] = rr_txns
                            if page_verification_out is not None:
                                page_verification_out[rr_page_num + 1] = "needs_review"
                            logger.info(
                                f"[R2] Page {rr_page_num + 1} replaced: "
                                f"R2 score {r2_score:.2f} > R1 score {r1_score:.2f}"
                            )
                        else:
                            logger.info(
                                f"[R2] Page {rr_page_num + 1} kept R1 result: "
                                f"R1 score {r1_score:.2f} ≥ R2 score {r2_score:.2f}"
                            )

            # Assemble all transactions in page order.
            all_transactions: List[Dict[str, Any]] = []
            for page_num in range(page_count):
                all_transactions.extend(page_results.get(page_num, []))

            if bank_type == "BEA":
                all_transactions = BankStatementParser._bea_post_filter_transactions(
                    all_transactions
                )
                BankStatementParser._bea_forward_fill_transaction_dates(all_transactions)
            elif bank_type == "HANG_SENG":
                BankStatementParser._bea_forward_fill_transaction_dates(all_transactions)
            elif bank_type == "OCBC":
                from app.services.ocbc_amount_repair import apply_ocbc_amount_policy

                all_transactions = apply_ocbc_amount_policy(all_transactions)
                if page_verification_out is not None:
                    for txn in all_transactions:
                        if not txn.get("_needs_review"):
                            continue
                        page_no = txn.get("_page")
                        try:
                            page_i = int(page_no)
                        except (TypeError, ValueError):
                            continue
                        if page_i >= 1:
                            page_verification_out[page_i] = "needs_review"

            logger.info(f"[BANK] Dual-track total: {len(all_transactions)} transactions")
            return all_transactions

        except Exception as e:
            logger.error(f"[BANK] Dual-track VLM pipeline failed: {e}", exc_info=True)
            return []
    
    def _extract_transactions_from_ai_response(
        self,
        ai_response: Dict,
        company_identity: Dict[str, Any] | None = None,
        *,
        filter_balance_anchor_rows: bool = True,
        reconcile_mode: str = "delta",
    ) -> List[Dict]:
        """Extract transaction list from AI-parsed response.

        When ``filter_balance_anchor_rows`` is False (e.g. HSBC AR manager merge),
        rows whose description looks like a balance anchor (B/F, opening balance, …)
        are kept so row counts stay aligned with the bookkeeper draft.

        ``reconcile_mode``: ``delta`` runs ``_reconcile_amounts_by_balance``;
        ``none`` skips it (OCBC gated policy runs after full-statement assemble).
        """
        # AI should return structured data with transactions array
        if isinstance(ai_response, dict):
            if 'transactions' in ai_response:
                transactions = ai_response['transactions']
            elif 'items' in ai_response:
                transactions = ai_response['items']
            elif 'data' in ai_response and isinstance(ai_response['data'], list):
                transactions = ai_response['data']
            elif 'raw_text' in ai_response and isinstance(ai_response['raw_text'], str):
                parsed_rows = self._parse_table_text(
                    ai_response['raw_text'],
                    company_identity=company_identity,
                )
                if reconcile_mode == "none":
                    return finalize_bank_transactions(list(parsed_rows))
                return finalize_bank_transactions(
                    list(self._reconcile_amounts_by_balance(parsed_rows))
                )
            else:
                logger.warning(f"Unexpected AI response format: {type(ai_response)}")
                return []
        elif isinstance(ai_response, list):
            transactions = ai_response
        else:
            logger.warning(f"Unexpected AI response format: {type(ai_response)}")
            return []

        # These rows act as balance anchors for reconciliation; must pass through
        # _reconcile_amounts_by_balance so it can use them as prev_balance seeds,
        # but are stripped from the final output afterwards.
        _BALANCE_ANCHOR_MARKERS = {
            # BOC 承前結餘 / 承前结余: kept in output (Balance B/F row); see boc.py prompt.
            # Still strip period summaries:
            "今期結餘", "今期结余",
            "賬戶結餘", "账户结余", "合計", "合计",
            # OCBC / English generic
            "B/F BALANCE", "B/F", "CARRIED FORWARD", "CARRY FORWARD",
            "OPENING BALANCE", "CLOSING BALANCE", "BROUGHT FORWARD",
            "TRANSACTION SUMMARY", "BALANCE B/F",
            # OCBC TRANSACTION SUMMARY count line — "8 8 ITEM(S)" pattern.
            # The VLM sometimes treats the item count as a transaction; description
            # and account_type both read "ITEM(S)" in those rows.
            "ITEM(S)", "ITEM(S) AMOUNT",
            # BOCOM / bilingual statement footers — period totals & counts, not txns.
            "TOTAL TRANSACTION AMOUNT",
            "交易總金額",
            "交易笔数",
            "交易筆數",
            "NO.OF TRANSACTION",
            "NO. OF TRANSACTION",
            # Generic English
            "opening balance", "closing balance",
        }

        normalized = []
        for txn in transactions:
            if not isinstance(txn, dict):
                continue
            normalized.append(self._normalize_transaction(txn, company_identity=company_identity))

        if reconcile_mode == "none":
            reconciled = normalized
        else:
            reconciled = self._reconcile_amounts_by_balance(normalized)

        # Remove pure balance-anchor rows after reconciliation (they have no real amounts).
        # Uses CONTAINS (not exact match) so concatenated strings like
        # "CARRIED FORWARD TRANSACTION SUMMARY AMOUNT ITEM(S)" are also caught.
        _markers_upper = {m.upper() for m in _BALANCE_ANCHOR_MARKERS}

        def _is_anchor_row(txn: dict) -> bool:
            desc = str(txn.get("備註") or txn.get("description") or "").strip().upper()
            # Also check account_type: "ITEM(S)" as account_type is another hallucination
            # pattern where the VLM reads the TRANSACTION SUMMARY count line.
            acct = str(
                txn.get("賬戶類型") or txn.get("帳戶類型") or txn.get("account_type") or ""
            ).strip().upper()
            if "BALANCE BROUGHT FORWARD" in desc:
                return False
            # OCBC/HK: printed opening row is often "BALANCE B/F" or "B/F BALANCE" — must not
            # be stripped as a generic anchor (see debug-82bf0f H1: same as BROUGHT FORWARD).
            if "BALANCE B/F" in desc or "B/F BALANCE" in desc:
                return False
            if desc.strip() in ("B/F",):
                return False
            return any(marker in desc for marker in _markers_upper) or \
                   any(marker in acct for marker in _markers_upper)

        if filter_balance_anchor_rows:
            filtered = [t for t in reconciled if not _is_anchor_row(t)]
        else:
            filtered = list(reconciled)

        # ── Deduplication: remove rows whose (running balance, account_type) pair has
        # already appeared on this page.  Repeated balances within the SAME account
        # section are the primary hallucination fingerprint (the VLM echoes the same
        # transaction set with the same balance sequence).
        # Keying by (balance, account_type) prevents cross-account false-positives:
        # two different account sections can legitimately share a balance value.
        seen_bal: set = set()
        deduped: list = []
        for txn in filtered:
            bal_raw = txn.get("原幣結餘") or txn.get("balance")
            acct_key = str(
                txn.get("賬戶類型") or txn.get("帳戶類型") or txn.get("account_type") or ""
            ).strip()
            desc_raw = str(txn.get("備註") or txn.get("description") or "")
            desc_u = desc_raw.upper()
            is_bf_opening = (
                "承前轉結" in desc_raw
                or "承上餘額" in desc_raw
                or "承上結餘" in desc_raw
                or "承上结余" in desc_raw
                or "承前結餘" in desc_raw
                or "承前结余" in desc_raw
                or "B/F BALANCE" in desc_u
                or "BALANCE B/F" in desc_u
                or desc_u.strip() == "B/F"
                or "OPENING BALANCE" in desc_u
                or "BROUGHT FORWARD" in desc_u
                or "BALANCE BROUGHT FORWARD" in desc_u
                or "\u627f\u524d\u7d50\u9918" in desc_raw
            )
            try:
                bal_float = round(float(str(bal_raw).replace(",", "")), 2) if bal_raw else None
            except (ValueError, TypeError):
                bal_float = None
            # HSBC B/F opening can repeat the same numeric balance as a later row.
            bal_key = (
                (bal_float, acct_key, "bf" if is_bf_opening else "txn")
                if bal_float is not None
                else None
            )
            if bal_key is not None and bal_key in seen_bal:
                logger.warning(
                    f"[DEDUP] Removed echoed transaction "
                    f"(balance {bal_float} / acct '{acct_key}' already seen): "
                    f"{str(txn.get('備註') or txn.get('description') or '')[:60]}"
                )
                continue
            if bal_key is not None:
                seen_bal.add(bal_key)
            deduped.append(txn)

        if not filter_balance_anchor_rows:
            logger.debug(
                "[BankParser] Manager extraction rows: raw=%d normalized=%d reconciled=%d filtered=%d deduped=%d",
                len(transactions),
                len(normalized),
                len(reconciled),
                len(filtered),
                len(deduped),
            )

        return finalize_bank_transactions(deduped)

    @staticmethod
    def _parse_numeric_amount(value: Any) -> float | None:
        text = str(value or "").strip()
        if not text or text in {"-", "—", "--", "None", "none", "N/A", "n/a"}:
            return None
        for symbol in ["$", "HK$", "HKD", "USD", "￥", "¥"]:
            text = text.replace(symbol, "")
        text = text.replace(",", "").strip()
        try:
            return float(text)
        except ValueError:
            return None

    @staticmethod
    def _format_amount(amount: float) -> str:
        fixed = f"{amount:.2f}"
        return fixed.rstrip("0").rstrip(".")

    def _reconcile_amounts_by_balance(
        self,
        transactions: List[Dict[str, Any]],
        tolerance: float = 0.01,
    ) -> List[Dict[str, Any]]:
        """Reconcile 存入/提取 by sequential 原幣結餘 delta, grouped by account context."""
        if not transactions:
            return transactions

        prev_balance_by_group: dict[tuple[str, str], float] = {}

        for txn in transactions:
            account_type = str(
                txn.get("賬戶類型")
                or txn.get("帳戶類型")
                or txn.get("account_type")
                or ""
            ).strip().lower()
            currency = str(txn.get("幣別") or txn.get("currency") or "HKD").strip().upper()
            group_key = (account_type or "__default__", currency)
            currency_key = ("__any__", currency)  # fallback if account_type label differs

            current_balance = self._parse_numeric_amount(
                txn.get("原幣結餘") or txn.get("balance") or txn.get("結餘") or txn.get("结余")
            )
            if current_balance is None:
                continue

            txn_type = str(txn.get("類型") or txn.get("type") or "").strip()
            description = str(txn.get("備註") or txn.get("description") or "").strip()
            joined = f"{txn_type} {description}"
            is_summary_row = any(
                marker in joined
                for marker in ["賬戶結餘", "账户结余", "今期結餘", "承前結餘", "無交易", "无交易"]
            )

            prev_balance = prev_balance_by_group.get(group_key)
            # No cross-account fallback: each (account_type, currency) group is independent.
            # Using currency_key as a fallback caused cross-section delta contamination —
            # the last balance of one account (e.g. 港元儲蓄) was used as the opening
            # reference for a different account (e.g. 港元往來), producing fabricated amounts.
            if prev_balance is not None and not is_summary_row:
                delta = current_balance - prev_balance
                if abs(delta) <= tolerance:
                    txn["存入"] = ""
                    txn["提取"] = ""
                    txn["received"] = ""
                    txn["spent"] = ""
                elif delta > 0:
                    amount_text = self._format_amount(delta)
                    txn["存入"] = amount_text
                    txn["提取"] = ""
                    txn["received"] = amount_text
                    txn["spent"] = ""
                else:
                    amount_text = self._format_amount(abs(delta))
                    txn["提取"] = amount_text
                    txn["存入"] = ""
                    txn["spent"] = amount_text
                    txn["received"] = ""

            prev_balance_by_group[group_key] = current_balance
            # Do NOT sync currency_key — cross-account fallback removed (see above).

        return transactions

    def _parse_table_text(
        self,
        text: str,
        company_identity: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        """Parse TSV/pipe-delimited table output into transaction dicts."""
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return []

        header_index = None
        for idx, line in enumerate(lines):
            has_no = "No." in line or "No" in line
            has_ref = "憑證號" in line or "凭证号" in line or "Reference" in line
            if has_no and has_ref:
                header_index = idx
                break

        if header_index is None:
            logger.warning("No table header found in AI output")
            return []

        header_line = lines[header_index]
        delimiter = "\t" if "\t" in header_line else "|"
        headers = [h.strip() for h in header_line.split(delimiter)]

        transactions: List[Dict[str, Any]] = []
        for line in lines[header_index + 1:]:
            if "No." in line and "憑證號" in line:
                continue
            parts = [p.strip() for p in line.split(delimiter)]
            if len(parts) < len(headers):
                continue
            row = {headers[i]: parts[i] for i in range(len(headers))}
            transactions.append(self._normalize_transaction(row, company_identity=company_identity))

        return transactions

    @staticmethod
    def _normalize_transaction(
        txn: Dict[str, Any],
        company_identity: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Normalize AI response keys to spreadsheet-friendly bank statement fields."""
        def pick(keys: list[str]) -> Any:
            for key in keys:
                value = txn.get(key)
                if value is not None and str(value).strip() != "":
                    return value
            return ""

        received = pick(["received", "deposit", "收入", "存入", "入帳", "credit", "cr"])
        spent = pick(["spent", "withdrawal", "支出", "提取", "出帳", "debit", "dr"])
        amount = pick(["amount", "金額", "金额"])
        txn_type = pick(["類型", "类型", "transaction_type"])

        description = pick(["description", "摘要", "交易摘要", "明細", "备注", "備註"])

        def _normalize_amount_token(value: Any) -> str:
            text = str(value or "").strip()
            if text in {"", "-", "—", "--", "None", "none", "N/A", "n/a"}:
                return ""
            compact = text.replace(",", "").replace("$", "").replace("HKD", "").strip()
            if compact in {"0", "0.0", "0.00"}:
                return ""
            return text

        received = _normalize_amount_token(received)
        spent = _normalize_amount_token(spent)

        def _parse_amount(value: Any) -> float:
            if value is None:
                return 0.0
            text = str(value).strip()
            if not text or text in {"-", "—"}:
                return 0.0
            for symbol in ["$", "HK$", "HKD", "USD", "￥", "¥"]:
                text = text.replace(symbol, "")
            text = text.replace(",", "")
            try:
                return float(text)
            except ValueError:
                return 0.0

        received_value = _parse_amount(received)
        spent_value = _parse_amount(spent)

        if not spent and not received and amount:
            type_hint = f"{txn_type}".lower()
            if any(token in type_hint for token in ["提取", "支出", "費用", "费用", "fee", "withdraw", "debit"]):
                spent = amount
            else:
                received = amount

            received_value = _parse_amount(received)
            spent_value = _parse_amount(spent)

        # Enforce ONLY one amount per row (存入 OR 提取)
        if received_value > 0 and spent_value > 0:
            type_hint = f"{txn_type} {description}".lower()
            if any(token in type_hint for token in ["提取", "支出", "費用", "费用", "fee", "withdraw", "debit"]):
                received = ""
            elif any(token in type_hint for token in ["存入", "收入", "存款", "deposit", "credit"]):
                spent = ""
            else:
                # Fall back to keeping the larger amount
                if received_value >= spent_value:
                    spent = ""
                else:
                    received = ""

        date_raw = pick(["date", "transaction_date", "交易日期", "日期", "入賬日期", "入账日期"])
        # Normalise YYYY/MM/DD → YYYY-MM-DD
        date_value = str(date_raw).replace("/", "-") if date_raw else date_raw
        # Normalise DDMONYY (OCBC/HK format) → YYYY-MM-DD: 29MAY25 → 2025-05-29
        if date_value:
            _mon_map = {
                'JAN': '01', 'FEB': '02', 'MAR': '03', 'APR': '04',
                'MAY': '05', 'JUN': '06', 'JUL': '07', 'AUG': '08',
                'SEP': '09', 'OCT': '10', 'NOV': '11', 'DEC': '12',
            }
            _dm = re.match(r'^(\d{1,2})([A-Za-z]{3})(\d{2})$', str(date_value).strip())
            if _dm:
                _dd, _mon, _yy = _dm.groups()
                _mm = _mon_map.get(_mon.upper())
                if _mm:
                    date_value = f'20{_yy}-{_mm}-{int(_dd):02d}'
        # description already resolved above for amount disambiguation
        reference = pick(["reference", "ref", "憑證號", "凭证号", "參考", "参考", "交易參考", "交易参考"])
        payment_ref = pick(["payment_ref", "付款參考", "付款参考", "Payment Ref", "payment reference"])
        source = pick(["source", "bank", "銀行", "银行", "Bank Transaction Source"])
        status = pick(["status", "狀態", "状态"])
        currency = pick(["currency", "幣別", "币别"]) or "HKD"
        payer = pick(["payer", "付款人"])
        payee = pick(["payee", "收款人"])
        account_type = pick(
            ["賬戶類型", "帳戶類型", "账户类型", "account_type", "account_name", "賬戶名稱", "帳戶名稱", "账户名称"]
        )
        account_category = pick(["categorise", "分類", "分类", "科目", "category", "account_category"])
        original_balance = pick(["原幣結餘", "原币结余", "balance", "結餘", "结余"])
        confidence = pick(["confidence", "confidence_score", "信心度", "置信度"])

        return {
            "date": date_value,
            "description": description,
            "reference": reference,
            "payment_ref": payment_ref,
            "spent": spent,
            "received": received,
            "source": source,
            "status": status,
            "幣別": currency,
            "存入": received or "",
            "提取": spent or "",
            "原幣結餘": original_balance,
            "日期": date_value,
            "備註": description,
            "憑證號": reference,
            "類型": txn_type,
            "付款人": payer,
            "收款人": payee,
            "銀行": source,
            "賬戶類型": account_type,
            "categorise": account_category,
            "分類": account_category,
            "category": account_category,
            "信心度": confidence
        }
    
    REQUIRED_CSV_HEADERS = {'Date', 'Description', 'Amount'}

    async def parse_csv(self, file_path: str) -> Dict[str, Any]:
        """Parse CSV bank statement"""
        logger.info(f"Parsing CSV: {file_path}")
        
        try:
            df = pd.read_csv(file_path)

            missing = self.REQUIRED_CSV_HEADERS - set(df.columns)
            if missing:
                raise ValueError(
                    f"CSV 欄位不符合範本格式。缺少欄位: {', '.join(sorted(missing))}。"
                    "請下載 CSV 範本並確保欄位名稱正確（Date, Description, Amount）。"
                )
            
            # Detect bank from CSV headers or content
            bank_name = self._detect_bank(df.to_string())
            
            transactions = []
            for _, row in df.iterrows():
                # Generic CSV parsing - adapt based on columns
                currency_raw = row.get('Currency', row.get('幣別', row.get('币别', 'HKD')))
                currency = str(currency_raw).strip() if currency_raw is not None and str(currency_raw).strip() else 'HKD'
                transaction = {
                    'date': str(row.get('Date', row.get('日期', ''))),
                    'description': str(row.get('Description', row.get('描述', row.get('交易摘要', '')))),
                    'amount': float(row.get('Amount', row.get('金額', 0))),
                    'balance': float(row.get('Balance', row.get('結餘', 0))) if 'Balance' in row or '結餘' in row else None,
                    'currency': currency,
                }
                transactions.append(transaction)
            
            return {
                'bank': bank_name,
                'transactions': transactions,
                'count': len(transactions)
            }
            
        except Exception as e:
            logger.error(f"CSV parsing failed: {e}")
            raise
    
    async def parse_excel(self, file_path: str) -> Dict[str, Any]:
        """Parse Excel bank statement"""
        logger.info(f"Parsing Excel: {file_path}")
        
        try:
            df = pd.read_excel(file_path)
            
            # Detect bank from Excel content
            bank_name = self._detect_bank(df.to_string())
            
            transactions = []
            for _, row in df.iterrows():
                # Generic Excel parsing - adapt based on columns
                currency_raw = row.get('Currency', row.get('幣別', row.get('币别', 'HKD')))
                currency = str(currency_raw).strip() if currency_raw is not None and str(currency_raw).strip() else 'HKD'
                transaction = {
                    'date': str(row.get('Date', row.get('日期', ''))),
                    'description': str(row.get('Description', row.get('描述', row.get('交易摘要', '')))),
                    'amount': float(row.get('Amount', row.get('金額', 0))),
                    'balance': float(row.get('Balance', row.get('結餘', 0))) if 'Balance' in row or '結餘' in row else None,
                    'currency': currency,
                }
                transactions.append(transaction)
            
            return {
                'bank': bank_name,
                'transactions': transactions,
                'count': len(transactions)
            }
            
        except Exception as e:
            logger.error(f"Excel parsing failed: {e}")
            raise
    
    # Helper methods
    
    def _is_date_field(self, text: str) -> bool:
        """Check if text contains a date pattern"""
        date_patterns = [
            r'\d{4}/\d{1,2}/\d{1,2}',  # YYYY/MM/DD or YYYY/M/D
            r'\d{1,2}/\d{1,2}/\d{4}',  # DD/MM/YYYY or D/M/YYYY
            r'\d{4}-\d{1,2}-\d{1,2}',  # YYYY-MM-DD
        ]
        for pattern in date_patterns:
            if re.search(pattern, text):
                return True
        return False
    
    def _parse_amount(self, amount_str: str) -> float:
        """Parse amount string, removing commas and handling empty values"""
        if not amount_str or amount_str.strip() in ['', '-', 'None']:
            return 0.0
        try:
            # Remove commas, spaces, and currency symbols
            cleaned = amount_str.replace(',', '').replace(' ', '').replace('$', '').replace('HKD', '').strip()
            return float(cleaned) if cleaned else 0.0
        except ValueError:
            logger.warning(f"Failed to parse amount: {amount_str}")
            return 0.0

    def _infer_account_type_from_text(self, text: str) -> str:
        """Infer account type/name from page-level statement header keywords."""
        haystack = (text or "").strip()
        if not haystack:
            return ""
        lowered = haystack.lower()

        # Prioritize more specific phrases first.
        patterns: list[tuple[list[str], str]] = [
            (["港元往來", "港元往来", "current account", "往來戶口", "往来户口"], "港元往來"),
            (["港元儲蓄", "港元储蓄", "savings account", "saving account", "儲蓄戶口", "储蓄户口"], "港元儲蓄"),
            (["外幣儲蓄", "外币储蓄", "foreign currency savings", "multi-currency"], "外幣儲蓄"),
            (["活期", "current"], "往來"),
        ]
        for keys, label in patterns:
            if any(key in lowered for key in keys):
                return label
        return ""
    
    def _infer_scb_account_type_from_text(self, text: str) -> str:
        """Infer SC account section label from page text.

        Returns SC-style section header labels that match what the VLM prompt
        uses (e.g. 'HKD Current Account 港元支票戶口') so table-parsed and
        VLM-parsed transactions carry consistent account_type values.
        """
        lowered = (text or "").lower()
        if not lowered:
            return "HKD Current Account 港元支票戶口"
        if "usd" in lowered or "us dollar" in lowered:
            if "savings" in lowered or "saving" in lowered:
                return "USD Savings Account 美元儲蓄戶口"
            return "USD Current Account 美元支票戶口"
        if "cny" in lowered or "renminbi" in lowered or "rmb" in lowered or "人民幣" in text:
            return "CNY Savings Account 人民幣儲蓄戶口"
        if "savings" in lowered or "saving" in lowered or "儲蓄" in text:
            return "HKD Savings Account 港元儲蓄戶口"
        return "HKD Current Account 港元支票戶口"

    def _normalize_date(self, date_str: str) -> str:
        """Normalize date string to YYYY-MM-DD format"""
        try:
            # Try different date formats
            for fmt in ['%Y/%m/%d', '%Y/%#m/%#d', '%d/%m/%Y', '%Y-%m-%d']:
                try:
                    dt = datetime.strptime(date_str.strip(), fmt)
                    return dt.strftime('%Y-%m-%d')
                except ValueError:
                    continue
            
            # If all formats fail, return original
            logger.warning(f"Could not parse date: {date_str}")
            return date_str
        except Exception as e:
            logger.warning(f"Date normalization failed for {date_str}: {e}")
            return date_str
    
    def _extract_reference(self, description: str) -> str:
        """Extract reference number (e.g., cheque number) from description"""
        # Look for patterns like: 0190630, CHQ 123456, etc.
        patterns = [
            r'\d{6,8}',  # 6-8 digit numbers (common for cheque numbers)
            r'CHQ\s*\d+',
            r'REF\s*\d+',
        ]
        for pattern in patterns:
            match = re.search(pattern, description)
            if match:
                return match.group(0)
        return ''
    
    def _classify_transaction(self, description: str) -> str:
        """Classify transaction type based on description"""
        desc_lower = description.lower()
        
        if '交換票' in description or 'cheque' in desc_lower or 'chq' in desc_lower:
            return 'cheque'
        elif '現金' in description or 'cash' in desc_lower or 'atm' in desc_lower:
            return 'cash'
        elif '轉賬' in description or 'transfer' in desc_lower or 'trf' in desc_lower:
            return 'transfer'
        elif '費用' in description or 'fee' in desc_lower or 'charge' in desc_lower:
            return 'fee'
        elif '退票' in description or 'returned' in desc_lower:
            return 'returned_cheque'
        else:
            return 'other'

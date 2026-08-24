"""
AI Post-Processor for OCR Results
Uses DeepSeek AI to improve OCR accuracy and extract structured data

Implementation based on sample-cheque-automation20260125-1
Uses requests library directly to avoid OpenAI SDK compatibility issues
"""
import json
import logging
import os
import re
from typing import Dict, Any, Optional
from datetime import datetime

from app.ocr.interfaces import OcrResult
from app.services.bank_account_type_coalesce import coalesce_bank_account_type_rows
from app.services.chart_of_accounts import get_prompt_account_lines
from app.services.ai_enhance_client import AiEnhanceClient, AiEnhanceResult
from app.core.config import settings

logger = logging.getLogger(__name__)


class AiPostProcessor:
    """
    Post-process OCR results using AI to improve accuracy
    
    Uses AiEnhanceClient for OpenAI-compatible chat completions
    which uses requests library directly to avoid OpenAI SDK compatibility issues
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_base: Optional[str] = None,
        model: Optional[str] = None,
        use_reasoner: Optional[bool] = None,
    ):
        """
        Initialize AI post-processor
        
        Args:
            api_key: DeepSeek API key (if None, reads from DEEPSEEK_API_KEY env var)
            api_base: DeepSeek API base URL
            model: Default model name
            use_reasoner: Whether to use DeepSeek Reasoner model
        """
        self.api_key = api_key if api_key is not None else settings.ai_enhance_api_key
        self.api_base = api_base if api_base is not None else settings.ai_enhance_api_base
        self.chat_model = model if model is not None else settings.ai_enhance_model
        self.reasoner_model = (
            os.getenv("AI_ENHANCE_REASONER_MODEL") or ""
        ).strip() or settings.ai_enhance_reasoner_model
        self.use_reasoner = (
            settings.ai_enhance_use_reasoner if use_reasoner is None else use_reasoner
        )
        self._service = None
    
    def _get_service(self) -> Optional[AiEnhanceClient]:
        """Lazy initialize AI enhancement client"""
        if self._service is None:
            try:
                self._service = AiEnhanceClient(
                    api_key=self.api_key,
                    base_url=self.api_base,
                    default_model=self.chat_model
                )
                logger.info(
                    f"[AI] AI service initialized (chat model: {self.chat_model}, "
                    f"reasoner enabled: {self.use_reasoner})"
                )
            except ValueError as e:
                # API key not configured
                logger.warning(f"[AI] {str(e)}. AI enhancement will be skipped.")
                self._service = None
            except Exception as e:
                logger.error(f"[AI] Failed to initialize AI service: {e}", exc_info=True)
                self._service = None
        return self._service
    
    async def enhance_ocr_result(
        self, 
        ocr_result: OcrResult,
        document_type: str = "cheque",
        processing_mode: str = "AR",
        metadata: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Use AI to enhance OCR results
        
        Args:
            ocr_result: Raw OCR result from EasyOCR
            document_type: Type of document (cheque, invoice, receipt, etc.)
        processing_mode: Processing mode (AR, AP, BANK)
            metadata: Additional metadata for document processing
        
        Returns:
            Enhanced structured data with corrections
        """
        service = self._get_service()
        
        if not service:
            logger.warning("AI enhancement API not configured. Returning raw OCR results.")
            return self._fallback_extraction(ocr_result)
        
        # Prepare OCR text for AI
        # Handle both OcrResult object and plain string
        if isinstance(ocr_result, str):
            ocr_text = ocr_result
        else:
            ocr_text = "\n".join([line.text for line in ocr_result.lines])

        if processing_mode == "BANK":
            ocr_text, was_trimmed, trim_limit = ocr_text, False, None
            logger.info("[AI] BANK mode: OCR length unlimited; skipping trim")
        else:
            ocr_text, was_trimmed, trim_limit = self._trim_ocr_text(ocr_text)
        
        logger.info(f"[AI] Processing {document_type} in {processing_mode} mode, OCR text length: {len(ocr_text)} chars")
        
        try:
            # Prepare system message for Hong Kong bookkeeping context (mode-specific)
            system_message = self._get_system_message(processing_mode)
            
            # Create user prompt based on document type and processing mode
            user_prompt = self._create_prompt(ocr_text, document_type, processing_mode, metadata)
            user_prompt = self._inject_company_context(user_prompt, metadata)
            include_summary = (
                os.getenv("AI_ENHANCE_INCLUDE_SUMMARY") or "false"
            ).lower() in ("true", "1", "yes")
            if include_summary:
                user_prompt += (
                    "\n\nAdd an \"analysis_summary\" field (1-3 sentences) explaining how key fields were identified. "
                    "Do not include chain-of-thought or hidden reasoning."
                )
            
            force_reasoner = (
                os.getenv("AI_ENHANCE_FORCE_REASONER") or "false"
            ).lower() in ("true", "1", "yes")
            if processing_mode in ("AR", "AP"):
                use_reasoner = force_reasoner
            elif processing_mode == "BANK":
                # BANK disables reasoner for per-page latency; model from AI_ENHANCE_MODEL.
                use_reasoner = False
            else:
                use_reasoner = (
                    force_reasoner
                    or self.use_reasoner
                    or len(ocr_text) > 2500
                )
            selected_model = self.reasoner_model if use_reasoner else self.chat_model
            logger.info(
                f"[AI] Mode: {processing_mode}, Model: {selected_model}, "
                f"Reasoner: {use_reasoner} (forced={force_reasoner}, "
                f"config={self.use_reasoner}, ocr_len={len(ocr_text)})"
            )
            logger.info(f"[AI] System message preview: {system_message[:200]}...")
            logger.info(f"[AI] Calling AI API (model: {selected_model})...")
            
            # Call AI enhancement API
            # Note: user_prompt contains the full prompt with instructions and OCR text
            max_tokens = self._get_max_tokens()
            if max_tokens:
                logger.info(f"[AI] Using max_tokens={max_tokens}")

            result: AiEnhanceResult = service.extract_fields_with_prompt(
                ocr_text=user_prompt,  # Full user prompt (includes instructions + OCR text)
                system_prompt=system_message,
                model=selected_model,
                max_tokens=max_tokens
            )
            
            logger.info(f"[AI] API call completed in {result.elapsed_time:.2f}s")
            logger.info(f"[AI] Received response, length: {len(result.raw)} chars")
            logger.debug(f"[AI] Response preview: {result.raw[:500]}...")
            
            # Parse AI response (already parsed as JSON in AiEnhanceClient)
            extracted_data = result.data
            
            # Extract reasoning content if available (Reasoner mode)
            reasoning_content = extracted_data.get("reasoning_content")
            if reasoning_content:
                logger.info(f"[AI] Received reasoning content, length: {len(reasoning_content)} chars")
                logger.debug(f"[AI] Reasoning preview: {reasoning_content[:300]}...")

            analysis_summary = extracted_data.get("analysis_summary")
            if analysis_summary:
                logger.info(f"[AI] analysis_summary: {analysis_summary}")
            
            # Ensure it's a dict (should already be from AiEnhanceClient)
            if not isinstance(extracted_data, dict):
                logger.warning("[AI] Response is not a dict, attempting to parse")
                extracted_data = self._parse_ai_response(result.raw)

            # If DeepSeek returned raw text, attempt JSON extraction here
            if isinstance(extracted_data, dict) and "raw_text" in extracted_data:
                # AR/AP/BANK prefer TSV output. Parse rows before JSON fallback.
                if processing_mode in ("AR", "AP"):
                    tsv_rows = self._parse_tsv_rows(extracted_data.get("raw_text", ""))
                    if tsv_rows:
                        extracted_data = {
                            "tsv_rows": tsv_rows,
                            "output_format": "tsv",
                        }
                elif processing_mode == "BANK":
                    tsv_rows = self._parse_tsv_rows_preserve(extracted_data.get("raw_text", ""))
                    if tsv_rows:
                        tsv_rows = coalesce_bank_account_type_rows(tsv_rows)
                        extracted_data = {
                            "tsv_rows": tsv_rows,
                            "output_format": "tsv",
                        }
                if "raw_text" in extracted_data and processing_mode != "BANK":
                    parsed = self._parse_ai_response(extracted_data.get("raw_text", ""))
                    if not parsed.get("error"):
                        extracted_data = parsed

            if processing_mode == "BANK" and isinstance(extracted_data, dict):
                has_rows = bool(extracted_data.get("tsv_rows") or extracted_data.get("transactions"))
                if not has_rows:
                    raw_tsv = result.raw if isinstance(result.raw, str) else ""
                    if not raw_tsv.strip():
                        raw_tsv = str(extracted_data.get("raw_text") or "")
                    tsv_rows = self._parse_tsv_rows_preserve(raw_tsv)
                    if tsv_rows:
                        tsv_rows = coalesce_bank_account_type_rows(tsv_rows)
                        extracted_data["tsv_rows"] = tsv_rows
                        extracted_data["output_format"] = "tsv"
            
            if processing_mode == "BANK" and isinstance(extracted_data, dict):
                bank_rows = extracted_data.get("tsv_rows")
                if isinstance(bank_rows, list) and bank_rows:
                    extracted_data["tsv_rows"] = coalesce_bank_account_type_rows(bank_rows)
            if isinstance(extracted_data, dict):
                page_num = (metadata or {}).get("page_num")
                if page_num is not None:
                    for key in ("tsv_rows", "transactions", "receipts", "rows"):
                        rows = extracted_data.get(key)
                        if not isinstance(rows, list):
                            continue
                        for row in rows:
                            if isinstance(row, dict) and row.get("_page") is None:
                                try:
                                    row["_page"] = int(page_num)
                                except (TypeError, ValueError):
                                    pass
            
            logger.info(f"[AI] Parsed response into {type(extracted_data).__name__}")
            
            # Add metadata including reasoning if available
            extracted_data["ai_processed"] = True
            extracted_data["confidence"] = self._calculate_confidence(extracted_data)
            extracted_data["raw_ocr_text"] = ocr_text
            extracted_data["context_meta"] = self._build_context_meta(metadata)
            if reasoning_content:
                extracted_data["reasoning_content"] = reasoning_content
                logger.info(f"[AI] Stored reasoning chain-of-thought ({len(reasoning_content)} chars)")
            
            return extracted_data
            
        except Exception as e:
            logger.error(f"[AI] Post-processing failed: {e}", exc_info=True)
            warning = self._build_timeout_warning(
                error=e,
                ocr_length=len(ocr_text),
                was_trimmed=was_trimmed,
                trim_limit=trim_limit
            )
            return self._fallback_extraction(ocr_result, warning=warning)

    def _inject_company_context(self, user_prompt: str, metadata: Optional[Dict[str, Any]]) -> str:
        """Inject company profile/rules context into the prompt when available."""
        if not metadata:
            return user_prompt

        context = metadata.get("company_context")
        if not isinstance(context, dict):
            return user_prompt

        profile = context.get("profile") if isinstance(context.get("profile"), dict) else {}
        rules = context.get("rules") if isinstance(context.get("rules"), list) else []

        if not profile and not rules:
            return user_prompt

        context_block = {
            "company_id": context.get("company_id"),
            "profile": {
                "industry": profile.get("industry"),
                "accounting_basis": profile.get("accounting_basis"),
                "fiscal_year_end": profile.get("fiscal_year_end"),
                "company_name": profile.get("company_name"),
                "company_name_keywords": profile.get("company_name_keywords", []),
                "custom_settings": profile.get("custom_settings", {}),
            },
            "rules": rules,
        }

        return (
            "Company Context (must be applied when relevant):\n"
            f"{json.dumps(context_block, ensure_ascii=False)}\n\n"
            "Prioritize these company-specific rules over generic assumptions.\n\n"
            f"{user_prompt}"
        )

    def _build_context_meta(self, metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not metadata:
            return {"company_context_used": False, "rule_count": 0, "rule_names": []}
        context = metadata.get("company_context")
        if not isinstance(context, dict):
            return {"company_context_used": False, "rule_count": 0, "rule_names": []}

        rules = context.get("rules") if isinstance(context.get("rules"), list) else []
        rule_names = [
            str(rule.get("name"))
            for rule in rules
            if isinstance(rule, dict) and rule.get("name")
        ]
        return {
            "company_context_used": True,
            "company_id": context.get("company_id"),
            "rule_count": len(rules),
            "rule_names": rule_names[:10],
            "industry": (context.get("profile") or {}).get("industry"),
            "accounting_basis": (context.get("profile") or {}).get("accounting_basis"),
            "company_name": (context.get("profile") or {}).get("company_name"),
            "company_name_keywords": (
                (context.get("profile") or {}).get("company_name_keywords") or []
            )[:20],
        }
    
    def _get_system_message(self, processing_mode: str) -> str:
        """Get mode-specific system message for AI"""
        
        if processing_mode == "AP":
            return (
                "You are a meticulous Hong Kong Accounts Payable (AP) Specialist. "
                "Your primary goal is to accurately process and verify all incoming invoices and payment documents for timely "
                "and correct payment to vendors. "
                "\n\n**Core Responsibilities:**\n"
                "- Verify the authenticity and accuracy of invoices, payment slips, and issued cheques.\n"
                "- Extract key information for bookkeeping and payment processing.\n"
                "- Categorize expenses according to standard accounting practices.\n"
                "- Flag any discrepancies or missing information for review.\n"
                "\n**Key Information to Extract:**\n"
                "- Vendor/Supplier Name\n"
                "- Invoice Number\n"
                "- Invoice Date\n"
                "- Due Date\n"
                "- Total Amount (in HKD)\n"
                "- Line items (Description, Quantity, Unit Price, Total)\n"
                "- Bank Account Information (if available)\n"
                "- Expense Category\n"
                "\n**Context and Constraints:**\n"
                "- You are an expert in Hong Kong's financial document formats, including common invoice layouts and banking "
                "conventions.\n"
                "- You can read and understand both Traditional Chinese and English.\n"
                "- All currency is in Hong Kong Dollars (HKD) unless explicitly stated otherwise.\n"
                "\n**Output Format:**\n"
                "- Provide the extracted information in a structured JSON format. Each key should correspond to the "
                "\"Key Information to Extract\" list.\n"
                "\n**Handling Ambiguity:**\n"
                "- If a document is illegible or missing critical information (e.g., vendor name, total amount), flag it for "
                "manual review and provide a brief explanation of the issue."
            )
        elif processing_mode == "AR":
            return (
                "你是一位会计专员，擅长从发票中提取结构化信息。请仔细阅读以下发票内容，并提取以下字段：\n\n"
                "字段说明：\n"
                "金额：发票的总金额（通常以“总计”或“Total”表示，注意可能是税前或税后，但这里以发票上明确标注的总计为准）。\n"
                "币别：发票金额的货币单位（如HKD、USD等）。\n"
                "日期：发票开具的日期（注意可能是发票顶部或底部的日期）。\n"
                "付款人：发票中指定的客户（即需要付款的一方）。\n"
                "收款人：发票的开具方（即提供货物或服务并收款的一方）。\n\n"
                "注意：\n\n"
                "金额提取时，请忽略任何折扣或实收金额，只提取发票的总计金额。\n\n"
                "币别如果未明确写出，可根据上下文推断，如“港币”即为HKD。\n\n"
                "日期请统一格式为YYYY-MM-DD。\n\n"
                "付款人和收款人请提取完整的公司名称。"
            )
        elif processing_mode == "BANK":
            return (
                "Role: You are a Hong Kong Bank Statement Data Extraction Specialist.\n\n"
                "Objective: Extract transaction data from OCR text and output TSV.\n\n"
                "Output Format:\n"
                "- Output MUST be TSV with the following 15 columns:\n"
                "  No.\t憑證號\t類型\t存入\t提取\t原幣結餘\t幣別\t日期\t付款人\t收款人\t銀行\t賬戶類型\t備註\tcategorise\t信心度\n"
                "- First line MUST be the exact header above.\n"
                "- Return ONLY TSV rows, no extra text.\n\n"
                "Rules:\n"
                "- Keep one-side amount: either 存入 or 提取.\n"
                "- 原幣結餘 is per-row statement balance after transaction.\n"
                "- Confirm 存入/提取 primarily by sequential balance math: delta = current 原幣結餘 - previous 原幣結餘.\n"
                "- Do NOT use 付款人/收款人 to determine 存入/提取 direction.\n"
                "- 賬戶類型 must be the printed account SECTION header only (e.g. 港元儲蓄, HKD STATEMENT SAVINGS).\n"
                "- NEVER put transaction detail lines in 賬戶類型: not 轉帳收入, 轉賬收入, 利息收入, 利息支出, CHARGES, or payee names — use 類型 or 備註 instead.\n"
                "- Even accounts with no transactions must output one summary row (類型=賬戶結餘, 備註=無交易) with closing 原幣結餘.\n"
                "- 日期 normalize to YYYY-MM-DD.\n"
                "- 付款人/收款人:\n"
                "  - Fill as contextual fields after direction is determined by balance math.\n"
                "  - 存入: 付款人=Unknown, 收款人=account holder\n"
                "  - 提取: 付款人=account holder, 收款人=Unknown\n"
                "- 銀行 from statement header.\n"
                "- Skip non-transaction summary rows (承前結餘/今期結餘/合計).\n\n"
                "- categorise rules (keep simple):\n"
                "  - If 備註/類型 indicates bank fee/service fee -> 5080 Bank Fee\n"
                "  - If it indicates interest charged/利息支出 -> 5090 Interest Paid\n"
                "  - If it indicates interest received/利息收入 -> 4030 Interest Received\n"
                "  - Most rows should be left empty for categorise (to be assigned via AR/AP reconciliation later).\n\n"
                "IMPORTANT: Your response MUST be ONLY the TSV table with header and data rows, no additional text.\n\n"
                "Now, process the following OCR text from a bank statement:"
            )
        else:
            # Default to AR for backward compatibility, with the improved prompt
            return (
                "你是一位会计专员，擅长从发票中提取结构化信息。请仔细阅读以下发票内容，并提取以下字段：\n\n"
                "字段说明：\n"
                "金额：发票的总金额（通常以“总计”或“Total”表示，注意可能是税前或税后，但这里以发票上明确标注的总计为准）。\n"
                "币别：发票金额的货币单位（如HKD、USD等）。\n"
                "日期：发票开具的日期（注意可能是发票顶部或底部的日期）。\n"
                "付款人：发票中指定的客户（即需要付款的一方）。\n"
                "收款人：发票的开具方（即提供货物或服务并收款的一方）。\n\n"
                "注意：\n\n"
                "金额提取时，请忽略任何折扣或实收金额，只提取发票的总计金额。\n\n"
                "币别如果未明确写出，可根据上下文推断，如“港币”即为HKD。\n\n"
                "日期请统一格式为YYYY-MM-DD。\n\n"
                "付款人和收款人请提取完整的公司名称。"
            )

    @staticmethod
    def _get_max_tokens() -> Optional[int]:
        value = os.getenv("AI_ENHANCE_MAX_TOKENS")
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            logger.warning(f"[AI] Invalid AI_ENHANCE_MAX_TOKENS: {value}")
            return None

    @staticmethod
    def _build_timeout_warning(
        error: Exception,
        ocr_length: int,
        was_trimmed: bool,
        trim_limit: Optional[int]
    ) -> Optional[str]:
        message = str(error).lower()
        if "timeout" not in message:
            return None
        if was_trimmed or (trim_limit and ocr_length >= trim_limit):
            return (
                "AI enhancement timeout. OCR text may be too large; "
                "please upload a smaller file or split pages."
            )
        return "AI enhancement timeout. Please try again."

    @staticmethod
    def _trim_ocr_text(ocr_text: str) -> tuple[str, bool, Optional[int]]:
        max_chars = os.getenv("AI_ENHANCE_OCR_MAX_CHARS", "6000")
        try:
            limit = int(max_chars)
        except ValueError:
            logger.warning(f"[AI] Invalid AI_ENHANCE_OCR_MAX_CHARS: {max_chars}")
            return ocr_text, False, None

        if limit <= 0 or len(ocr_text) <= limit:
            return ocr_text, False, limit

        marker = "\n...[TRIMMED]...\n"
        head_len = int(limit * 0.7)
        tail_len = max(limit - head_len - len(marker), 0)
        trimmed = f"{ocr_text[:head_len]}{marker}{ocr_text[-tail_len:]}" if tail_len else f"{ocr_text[:limit]}"
        logger.warning(f"[AI] OCR text trimmed from {len(ocr_text)} to {len(trimmed)} chars")
        return trimmed, True, limit
    
    def _create_prompt(self, ocr_text: str, document_type: str, processing_mode: str = "AR", metadata: Dict[str, Any] = None) -> str:
        """Create AI prompt based on document type"""
        
        if metadata is None:
            metadata = {}

        if processing_mode == "BANK":
            return f"OCR Text:\n{ocr_text}"

        category_prompt_block = ""
        if processing_mode in ("AR", "AP"):
            category_lines = get_prompt_account_lines(processing_mode)
            lines_text = "\n".join(category_lines)
            if processing_mode == "AR":
                restriction = (
                    "AR mode allows ONLY revenue/income categories. "
                    "Do NOT output expense categories. "
                    "If unclear, use 4050 Other Income / 其他收入."
                )
            else:
                restriction = (
                    "AP mode allows ONLY expense/COGS categories. "
                    "Do NOT output revenue categories. "
                    "If unclear, use 5110 Other Expense / 其他支出."
                )
            category_prompt_block = (
                "\n**Account Category Rules (mandatory)**:\n"
                f"{restriction}\n"
                "Choose exactly ONE category from this list:\n"
                f"{lines_text}\n"
                "Return the selected category in `account_category` as "
                "\"<code> <name_en>\" (for example: \"5030 Office Supplies\").\n"
            )

        category_prompt_block = ""
        if processing_mode in ("AR", "AP"):
            category_lines = get_prompt_account_lines(processing_mode)
            lines_text = "\n".join(category_lines)
            if processing_mode == "AR":
                restriction = (
                    "AR mode allows ONLY revenue/income categories. "
                    "Do NOT output expense categories. "
                    "If unclear, use 4050 Other Income / 其他收入."
                )
            else:
                restriction = (
                    "AP mode allows ONLY expense/COGS categories. "
                    "Do NOT output revenue categories. "
                    "If unclear, use 5110 Other Expense / 其他支出."
                )
            category_prompt_block = (
                "\n**Account Category Rules (mandatory)**:\n"
                f"{restriction}\n"
                "Choose exactly ONE category from this list:\n"
                f"{lines_text}\n"
                "Return the selected category in `account_category` as "
                "\"<code> <name_en>\" (for example: \"5030 Office Supplies\").\n"
            )

        category_prompt_block = ""
        if processing_mode in ("AR", "AP"):
            category_lines = get_prompt_account_lines(processing_mode)
            lines_text = "\n".join(category_lines)
            if processing_mode == "AR":
                restriction = (
                    "AR mode allows ONLY revenue/income categories. "
                    "Do NOT output expense categories. "
                    "If unclear, use 4050 Other Income / 其他收入."
                )
            else:
                restriction = (
                    "AP mode allows ONLY expense/COGS categories. "
                    "Do NOT output revenue categories. "
                    "If unclear, use 5110 Other Expense / 其他支出."
                )
            category_prompt_block = (
                "\n**Account Category Rules (mandatory)**:\n"
                f"{restriction}\n"
                "Choose exactly ONE category from this list:\n"
                f"{lines_text}\n"
                "Return the selected category in `account_category` as "
                "\"<code> <name_en>\" (for example: \"5030 Office Supplies\").\n"
            )
        
        if document_type == "cheque":
            # Mode-specific context
            mode_context = ""
            if processing_mode == "AP":
                mode_context = """
**AP Mode Context**:
- This cheque is ISSUED (payable) - your company is paying out
- Focus on VENDOR/SUPPLIER identification
- Track payment authorization and expense categorization
- Payer should be your company name
- Payee is the vendor/supplier receiving payment
"""
            elif processing_mode == "AR":
                mode_context = """
**AR Mode Context**:
- This cheque is RECEIVED (receivable) - your company is receiving payment
- Focus on CUSTOMER identification
- Track revenue recognition and deposit confirmation
- Payer is the customer making payment
- Payee should be your company name
"""
            return f"""
You are processing a cheque for Hong Kong bookkeeping. Extract data accurately for spreadsheet entry.

{mode_context}

**Context**: 
- This cheque will be recorded in accounts receivable (AR) or accounts payable (AP)
- Data must be accurate for financial reconciliation
- Support both English and Traditional Chinese text

**OCR Text**:
{ocr_text}

**Instructions**:
1. Fix common OCR errors: 0↔O, 1↔I↔l, 8↔B, 5↔S, 2↔Z
2. Validate financial data (amount, date, cheque number)
3. Identify Hong Kong banks (HSBC/滙豐, Hang Seng/恆生, Bank of China/中銀, Standard Chartered/渣打, etc.)
4. Determine transaction type (AR for cheques received, AP for cheques issued)
5. Prepare clean data for spreadsheet input

**Extract in JSON format**:
{{
    "cheque_number": "6-8 digit numeric code",
    "date": "YYYY-MM-DD (validate as real date)",
    "payee": "recipient/收款人 full name",
    "payer": "drawer/付款人 full name",
    "amount_numeric": "exact numeric amount (e.g., 10000.00)",
    "amount_words": "amount in words/大寫 (in Chinese if available)",
    "currency": "HKD (default if not specified)",
    "bank_name": "full bank name in English",
    "bank_code": "3-digit bank code if visible (e.g., 004 for HSBC)",
    "account_number": "account number if visible",
    "memo": "payment purpose/memo/備註",
    "transaction_type": "AR or AP",
    "cheque_type": "bearer, order, crossed, etc.",
    "errors_corrected": ["list of OCR corrections made"],
    "warnings": ["validation warnings or data quality issues"],
    "confidence_notes": "overall data quality assessment"
}}

**Validation Rules**:
✓ Amount in words must match numeric amount
✓ Date must be valid (not future date unless post-dated)
✓ Cheque number must be numeric (6-8 digits typical)
✓ Payee and Payer must be different
✓ Bank name must be a recognized HK bank
✓ Currency defaults to HKD

**Transaction Type Logic**:
- AR (Accounts Receivable): If this company is the PAYEE (receiving money)
- AP (Accounts Payable): If this company is the PAYER (paying out)
- If unclear from context, mark as "AR" (default for received cheques)

Return ONLY valid JSON. No extra text or markdown.
Return null for missing fields. Be precise with financial data.
"""
        
        elif document_type == "invoice":
            if processing_mode == "AP":
                return f"""
You are analyzing AP documents and this OCR text may contain MULTIPLE receipts/invoices mixed on one page.

Follow this strict 4-step logic:
STEP 1) Identify all merchant/vendor anchors.
STEP 2) For each anchor, assign nearest amount/date by proximity and structure.
STEP 3) Detect duplicates (same merchant + amount + date).
STEP 4) Validate completeness and confidence.
{category_prompt_block}

OCR Text:
{ocr_text}

Return ONLY valid JSON in this shape:
{{
  "multi_receipt_detected": true or false,
  "receipt_count": 2,
  "receipts": [
    {{
      "id": 1,
      "vendor": "string",
      "invoice_number": "string|null",
      "date": "YYYY-MM-DD|null",
      "currency": "HKD",
      "total_amount": "numeric|string|null",
      "account_category": "string|null",
      "is_duplicate": false,
      "duplicate_of": null,
      "confidence": 0.0
    }}
  ],
  "invoice_number": "primary receipt invoice number or null",
  "date": "primary receipt date or null",
  "vendor": "primary receipt vendor or null",
  "customer": "primary receipt customer or null",
  "currency": "HKD",
  "total_amount": "primary receipt amount or null",
  "account_category": "string|null",
  "items": [],
  "reasoning": "short 4-step summary"
}}
"""
            return f"""
Extract invoice data from this OCR text:

{ocr_text}

Return ONLY valid JSON with:
{{
    "invoice_number": "string",
    "date": "YYYY-MM-DD",
    "vendor": "company name",
    "customer": "company name",
    "currency": "HKD (default if not specified)",
    "total_amount": "numeric",
    "items": [
        {{"description": "string", "amount": "numeric"}}
    ]
}}
"""
        
        elif document_type == "bank_statement":
            return f"""
Role: You are a Hong Kong Bank Statement Data Extraction Specialist.

Instructions:
Extract transaction data from the provided OCR text of a bank statement and output a structured JSON array. Each transaction should include the following 11 fields:

1. "No." - Sequential number starting from 1
2. "憑證號" - Reference/transaction number if available
3. "類型" - Transaction type/category
4. "金額" - Transaction amount (positive number, absolute value)
5. "幣別" - Currency code
6. "日期" - Transaction date in YYYY-MM-DD format
7. "付款人" - Payer (who pays the money)
8. "收款人" - Payee (who receives the money)
9. "銀行" - Bank name
10. "備註" - Notes/description
11. "信心度" - Confidence score (0-1)

Extraction Logic:

1. Transaction Detection:
   - Identify transaction rows by looking for date patterns followed by transaction descriptions and amounts.
   - Ignore header rows, balance rows (承前結餘, 本期結餘), and summary rows.

2. Field-Specific Rules:

   a. "類型" (Transaction Type):
      - Map from transaction description: 
        * "交換票", "CDM DEP" → "交換票存入"
        * "現金交易" → "現金交易" 
        * "銀行費用", "BIA FEE" → "銀行費用"
        * "退票" → "退票"
        * Default: Use first 4 words of description

   b. "金額" (Amount):
      - Extract from "存入" (deposit) or "提取" (withdrawal) columns.
      - If both exist, use the non-zero value.
      - Return absolute value as positive number.
      - IMPORTANT: Output ONLY one amount column in TSV: either 存入 OR 提取 (the other must be empty).

   c. "幣別" (Currency):
      - Infer from account name: "港元" → "HKD", "外幣" → check context.
      - Default to "HKD" for Hong Kong banks.

   d. "日期" (Date):
      - Use "交易日期" if available, otherwise "起息/生效日期".
      - Convert to YYYY-MM-DD.

   e. "付款人"/"收款人" (Payer/Payee):
      - For deposits (存入): Payer = "Unknown", Payee = Account holder
      - For withdrawals (提取): Payer = Account holder, Payee = "Unknown"
      - For fees: Payer = Account holder, Payee = Bank name
      - For transfers: Infer from description if possible

   f. "銀行" (Bank):
      - Extract from statement header (e.g., "中国银行(香港)")

   g. "備註" (Remarks):
      - Full transaction description excluding type keywords

   h. "信心度" (Confidence):
      - 0.9-1.0: Clear transaction with all fields
      - 0.7-0.9: Missing some details
      - 0.5-0.7: Ambiguous or incomplete

3. Handling OCR Artifacts:
   - Ignore misrecognized characters, extra spaces
   - Use context to correct OCR errors (e.g., "黄竹坑" → location indicator)
   - If a transaction is incomplete, extract available fields and set confidence lower

4. Bank-Specific Parsing:
   - Bank of China (中國銀行): Parse "交易日期", "起息/生效日期", "交易摘要", "存入", "提取", "原幣結餘" columns
   - HSBC/Hang Seng/Standard Chartered: Adapt to respective formats
   - Default: Look for date, description, amount patterns

Output Format:
- Output MUST be in TSV (tab-separated values) format with the following 12 columns:
  No.\t憑證號\t類型\t存入\t提取\t幣別\t日期\t付款人\t收款人\t銀行\t備註\t信心度
- First line MUST be the header row with column names exactly as above.
- Each subsequent line represents one transaction, with fields separated by tabs.
- Do not include any additional text, explanations, or formatting outside the TSV table.
 - Each row must have ONLY ONE amount value: 存入 or 提取 (never both).

Now process the provided bank statement OCR text and output the TSV table.
"""
        
        elif document_type == "bank_statement_page":
            bank = metadata.get('bank', 'UNKNOWN')
            page_num = metadata.get('page_num', 1)
            
            return f"""
Role: You are a Hong Kong Bank Statement Data Extraction Specialist.

This page contains MULTIPLE transactions in a table format. Extract ALL transactions from this page, not just one.

**OCR Text**:
{ocr_text}

Output a tab-separated table (TSV). Each transaction must include the following 12 fields:
1. "No." - Sequential number starting from 1
2. "憑證號" - Reference/transaction number if available
3. "類型" - Transaction type/category
4. "存入" - Deposit amount (positive number, MUST be empty if 提取 has value)
5. "提取" - Withdrawal amount (positive number, MUST be empty if 存入 has value)
6. "幣別" - Currency code
7. "日期" - Transaction date in YYYY-MM-DD format
8. "付款人" - Payer (who pays the money)
9. "收款人" - Payee (who receives the money)
10. "銀行" - Bank name
11. "備註" - Notes/description
12. "信心度" - Confidence score (0-1)

Important:
- Each transaction row must have ONLY ONE amount: either 存入 OR 提取.
- If both amounts appear in OCR, choose the non-zero one and leave the other blank.

Return ONLY the table, no additional text.
"""
        
        else:
            return f"""
Extract key information from this document:

{ocr_text}

Return JSON with relevant fields based on document content.
"""
    
    def _parse_ai_response(self, response: str) -> Dict[str, Any]:
        """Parse AI response to extract JSON data"""
        try:
            # Try to find JSON in response
            start_idx = response.find('{')
            end_idx = response.rfind('}') + 1
            
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                return json.loads(json_str)
            
            logger.warning("No JSON found in AI response")
            return {"error": "Failed to parse AI response", "raw_text": response}
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return {"error": "Invalid JSON from AI", "raw_text": response}
    
    def _calculate_confidence(self, extracted_data: Dict[str, Any]) -> float:
        """Calculate confidence score based on extracted data quality for HK bookkeeping"""
        confidence = 1.0
        
        # Check for missing critical fields (HK bookkeeping essentials)
        critical_fields = ["cheque_number", "date", "amount_numeric", "payee", "payer"]
        missing_fields = [f for f in critical_fields if not extracted_data.get(f)]
        
        if missing_fields:
            confidence -= 0.1 * len(missing_fields)
        
        # Check for missing important fields (nice to have)
        important_fields = ["bank_name", "transaction_type"]
        missing_important = [f for f in important_fields if not extracted_data.get(f)]
        
        if missing_important:
            confidence -= 0.05 * len(missing_important)
        
        # Check for warnings
        if extracted_data.get("warnings"):
            confidence -= 0.03 * len(extracted_data["warnings"])
        
        # Check for validation errors
        if extracted_data.get("error"):
            confidence -= 0.3
        
        # Bonus for having bank code (indicates good OCR quality)
        if extracted_data.get("bank_code"):
            confidence += 0.05
        
        # Bonus for having transaction type classified
        if extracted_data.get("transaction_type") in ["AR", "AP"]:
            confidence += 0.05
        
        return max(0.0, min(1.0, confidence))
    
    def _fallback_extraction(self, ocr_result: OcrResult | str, warning: Optional[str] = None) -> Dict[str, Any]:
        """Fallback extraction without AI (rule-based)"""
        if isinstance(ocr_result, str):
            raw_text = ocr_result
            lines = []
        else:
            raw_text = "\n".join([line.text for line in ocr_result.lines])
            lines = [
                {
                    "text": line.text,
                    "confidence": line.confidence
                }
                for line in ocr_result.lines
            ]
        payload = {
            "ai_processed": False,
            "raw_ocr_text": raw_text,
            "lines": lines,
            "warning": "AI post-processing not available. Using raw OCR results."
        }
        if warning:
            payload["warning"] = warning
        return payload

    @staticmethod
    def _parse_tsv_rows(raw_text: str) -> list[dict[str, Any]]:
        """Parse TSV output into canonical AR/AP row objects."""
        if not raw_text:
            return []

        text = raw_text.strip()
        if not text:
            return []

        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return []

        header = [col.strip() for col in lines[0].split("\t")]
        if len(header) < 3:
            return []

        rows: list[dict[str, Any]] = []
        for line in lines[1:]:
            cols = [col.strip() for col in line.split("\t")]
            if not any(cols):
                continue
            while len(cols) < len(header):
                cols.append("")
            raw_row = dict(zip(header, cols))
            normalized = AiPostProcessor._normalize_arap_row_aliases(raw_row)
            if any(str(v).strip() for v in normalized.values()):
                rows.append(normalized)
        return rows

    @staticmethod
    def _parse_tsv_rows_preserve(raw_text: str) -> list[dict[str, Any]]:
        """Parse TSV output keeping original column headers (for BANK 存入/提取 columns)."""
        if not raw_text:
            return []

        text = raw_text.strip()
        if not text:
            return []

        lines = [line for line in text.splitlines() if line.strip()]
        if not lines:
            return []

        header = [col.strip() for col in lines[0].split("\t")]
        if len(header) < 3:
            return []

        rows: list[dict[str, Any]] = []
        for line in lines[1:]:
            cols = [col.strip() for col in line.split("\t")]
            if not any(cols):
                continue
            while len(cols) < len(header):
                cols.append("")
            row = dict(zip(header, cols))
            if any(str(v).strip() for v in row.values()):
                rows.append(row)
        return rows

    @staticmethod
    def _normalize_arap_row_aliases(row: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize AR/AP row aliases to frontend canonical keys."""
        def pick(*keys: str) -> str:
            for key in keys:
                value = row.get(key)
                if value is None:
                    continue
                text = str(value).strip()
                if text:
                    return text
            return ""

        return {
            "voucher_no": pick("voucher_no", "憑證號", "voucher", "invoice_number", "cheque_number"),
            "transaction_type": pick("transaction_type", "類型"),
            "amount": pick("amount", "金額", "total_amount", "amount_numeric"),
            "currency": pick("currency", "幣別"),
            "date": pick("date", "日期"),
            "payer": pick("payer", "付款人"),
            "payee": pick("payee", "收款人", "vendor", "customer"),
            "bank": pick("bank", "銀行", "bank_name"),
            "category": pick("category", "categorise", "分類", "account_category"),
            "memo": pick("memo", "備註", "note", "notes"),
            "confidence": pick("confidence", "信心度"),
        }
    
    def _get_system_message(self, processing_mode: str) -> str:
        """Get mode-specific system message for AI"""
        
        if processing_mode == "AP":
            return (
                "Role: You are a Hong Kong AP extraction specialist.\n\n"
                "Objective: Parse OCR HTML/text and output TSV rows for AP bookkeeping.\n\n"
                "AP constraints:\n"
                "- AP documents represent payables/expenses.\n"
                "- One page may contain multiple receipts/invoices; output one TSV row per receipt.\n"
                "- Category must be AP-safe (expense/COGS categories only).\n"
                "- Use labels + proximity in HTML structure to map amount/date/payer/payee correctly.\n"
            )
        elif processing_mode == "AR":
            return (
                "Role: You are a Hong Kong AR extraction specialist.\n\n"
                "Objective: Parse OCR HTML/text and output TSV rows for AR bookkeeping.\n\n"
                "AR constraints:\n"
                "- AR documents represent receivables/income.\n"
                "- Output one TSV row per logical receipt/invoice/cheque on the page.\n"
                "- Category must be AR-safe (revenue/income categories only).\n"
                "- Use labels + proximity in HTML structure to map amount/date/payer/payee correctly.\n"
            )
        elif processing_mode == "BANK":
            return (
                "Role: You are a Hong Kong Bank Statement Data Extraction Specialist.\n\n"
                "Objective: Extract transaction data from OCR text and output TSV.\n\n"
                "Output Format:\n"
                "- Output MUST be TSV with the following 15 columns:\n"
                "  No.\t憑證號\t類型\t存入\t提取\t原幣結餘\t幣別\t日期\t付款人\t收款人\t銀行\t賬戶類型\t備註\tcategorise\t信心度\n"
                "- First line MUST be the exact header above.\n"
                "- Return ONLY TSV rows, no extra text.\n\n"
                "Rules:\n"
                "- Keep one-side amount: either 存入 or 提取.\n"
                "- 原幣結餘 is per-row statement balance after transaction.\n"
                "- Confirm 存入/提取 primarily by sequential balance math: delta = current 原幣結餘 - previous 原幣結餘.\n"
                "- Do NOT use 付款人/收款人 to determine 存入/提取 direction.\n"
                "- 賬戶類型 must be the printed account SECTION header only (e.g. 港元儲蓄, HKD STATEMENT SAVINGS).\n"
                "- NEVER put transaction detail lines in 賬戶類型: not 轉帳收入, 轉賬收入, 利息收入, 利息支出, CHARGES, or payee names — use 類型 or 備註 instead.\n"
                "- Even accounts with no transactions must output one summary row (類型=賬戶結餘, 備註=無交易) with closing 原幣結餘.\n"
                "- 日期 normalize to YYYY-MM-DD.\n"
                "- 付款人/收款人:\n"
                "  - Fill as contextual fields after direction is determined by balance math.\n"
                "  - 存入: 付款人=Unknown, 收款人=account holder\n"
                "  - 提取: 付款人=account holder, 收款人=Unknown\n"
                "- 銀行 from statement header.\n"
                "- Skip non-transaction summary rows (承前結餘/今期結餘/合計).\n\n"
                "- categorise rules (keep simple):\n"
                "  - If 備註/類型 indicates bank fee/service fee -> 5080 Bank Fee\n"
                "  - If it indicates interest charged/利息支出 -> 5090 Interest Paid\n"
                "  - If it indicates interest received/利息收入 -> 4030 Interest Received\n"
                "  - Most rows should be left empty for categorise (to be assigned via AR/AP reconciliation later).\n\n"
                "IMPORTANT: Your response MUST be ONLY the TSV table with header and data rows, no additional text."
            )
        else:
            # Default to AR for backward compatibility
            return (
                "你是一位会计专员，擅长从发票中提取结构化信息。请仔细阅读以下发票内容，并提取以下字段：\n\n"
                "字段说明：\n"
                "金额：发票的总金额（通常以“总计”或“Total”表示，注意可能是税前或税后，但这里以发票上明确标注的总计为准）。\n"
                "币别：发票金额的货币单位（如HKD、USD等）。\n"
                "日期：发票开具的日期（注意可能是发票顶部或底部的日期）。\n"
                "付款人：发票中指定的客户（即需要付款的一方）。\n"
                "收款人：发票的开具方（即提供货物或服务并收款的一方）。\n\n"
                "注意：\n\n"
                "金额提取时，请忽略任何折扣或实收金额，只提取发票的总计金额。\n\n"
                "币别如果未明确写出，可根据上下文推断，如“港币”即为HKD。\n\n"
                "日期请统一格式为YYYY-MM-DD。\n\n"
                "付款人和收款人请提取完整的公司名称。"
            )

    @staticmethod
    def _get_max_tokens() -> Optional[int]:
        value = os.getenv("AI_ENHANCE_MAX_TOKENS")
        if not value:
            return None
        try:
            return int(value)
        except ValueError:
            logger.warning(f"[AI] Invalid AI_ENHANCE_MAX_TOKENS: {value}")
            return None

    @staticmethod
    def _build_timeout_warning(
        error: Exception,
        ocr_length: int,
        was_trimmed: bool,
        trim_limit: Optional[int]
    ) -> Optional[str]:
        message = str(error).lower()
        if "timeout" not in message:
            return None
        if was_trimmed or (trim_limit and ocr_length >= trim_limit):
            return (
                "AI enhancement timeout. OCR text may be too large; "
                "please upload a smaller file or split pages."
            )
        return "AI enhancement timeout. Please try again."

    @staticmethod
    def _trim_ocr_text(ocr_text: str) -> tuple[str, bool, Optional[int]]:
        max_chars = os.getenv("AI_ENHANCE_OCR_MAX_CHARS", "6000")
        try:
            limit = int(max_chars)
        except ValueError:
            logger.warning(f"[AI] Invalid AI_ENHANCE_OCR_MAX_CHARS: {max_chars}")
            return ocr_text, False, None

        if limit <= 0 or len(ocr_text) <= limit:
            return ocr_text, False, limit

        marker = "\n...[TRIMMED]...\n"
        head_len = int(limit * 0.7)
        tail_len = max(limit - head_len - len(marker), 0)
        trimmed = f"{ocr_text[:head_len]}{marker}{ocr_text[-tail_len:]}" if tail_len else f"{ocr_text[:limit]}"
        logger.warning(f"[AI] OCR text trimmed from {len(ocr_text)} to {len(trimmed)} chars")
        return trimmed, True, limit
    
    def _create_prompt(self, ocr_text: str, document_type: str, processing_mode: str = "AR", metadata: Dict[str, Any] = None) -> str:
        """Create AI prompt based on document type"""
        
        if metadata is None:
            metadata = {}

        if processing_mode == "BANK":
            return f"OCR Text:\n{ocr_text}"

        category_prompt_block = ""
        if processing_mode in ("AR", "AP"):
            category_lines = get_prompt_account_lines(processing_mode)
            lines_text = "\n".join(category_lines)
            if processing_mode == "AR":
                restriction = (
                    "AR mode allows ONLY revenue/income categories. "
                    "Do NOT output expense categories. "
                    "If unclear, use 4050 Other Income / 其他收入."
                )
            else:
                restriction = (
                    "AP mode allows ONLY expense/COGS categories. "
                    "Do NOT output revenue categories. "
                    "If unclear, use 5110 Other Expense / 其他支出."
                )
            category_prompt_block = (
                "**Account Category Rules (mandatory)**:\n"
                f"{restriction}\n"
                "Choose exactly ONE category from this list:\n"
                f"{lines_text}\n"
                "Return the selected category in `category` (or `categorise`) as "
                "\"<code> <name_en>\" (for example: \"5030 Office Supplies\").\n"
            )

        if processing_mode in ("AR", "AP"):
            mode_hint = "AP" if processing_mode == "AP" else "AR"
            split_rule = (
                "If one page contains multiple receipts/invoices, output multiple rows."
                if processing_mode == "AP"
                else "If one page contains multiple logical transactions, output multiple rows."
            )
            return f"""
You are parsing OCR HTML/text for Hong Kong {mode_hint} bookkeeping.

Input may include HTML tables/divs from OCR. Read HTML structure and preserve row meaning.
Use labels + proximity to identify amount, currency, date, payer, payee, bank, memo, category.
{split_rule}

{category_prompt_block}

Output Format (TSV only):
voucher_no\ttransaction_type\tamount\tcurrency\tdate\tpayer\tpayee\tbank\tcategory\tmemo\tconfidence

Rules:
- Return ONLY TSV rows with the exact header above.
- One row per transaction/receipt/invoice/cheque.
- transaction_type must be AR or AP.
- date normalize to YYYY-MM-DD.
- currency default HKD when missing.
- amount must be numeric without thousands separators.
- confidence as 0-1 decimal.
- If field missing, leave empty (do not invent values).

OCR Text:
{ocr_text}
"""

        # If user confirmed this page has multiple receipts in AP mode,
        # force multi-receipt prompt regardless of document_type classifier.
        if processing_mode == "AP" and bool(metadata.get("multi_receipt_confirmed")):
            return f"""
You are analyzing AP documents and this OCR text may contain MULTIPLE receipts/invoices mixed on one page.

Follow this strict 4-step logic:
STEP 1) Identify all merchant/vendor anchors.
STEP 2) For each anchor, assign nearest amount/date by proximity and structure.
STEP 3) Detect duplicates (same merchant + amount + date).
STEP 4) Validate completeness and confidence.
{category_prompt_block}

OCR Text:
{ocr_text}

Return ONLY valid JSON in this shape:
{{
  "multi_receipt_detected": true or false,
  "receipt_count": 0,
  "receipts": [
    {{
      "id": 1,
      "vendor": "string",
      "invoice_number": "string|null",
      "date": "YYYY-MM-DD|null",
      "currency": "HKD",
      "total_amount": "numeric|string|null",
      "account_category": "string|null",
      "is_duplicate": false,
      "duplicate_of": null,
      "confidence": 0.0
    }}
  ],
  "invoice_number": "primary receipt invoice number or null",
  "date": "primary receipt date or null",
  "vendor": "primary receipt vendor or null",
  "customer": "primary receipt customer or null",
  "currency": "HKD",
  "total_amount": "primary receipt amount or null",
  "account_category": "string|null",
  "items": [],
  "reasoning": "short 4-step summary"
}}
"""
        
        if document_type == "cheque":
            # Mode-specific context
            mode_context = ""
            if processing_mode == "AP":
                mode_context = """
**AP Mode Context**:
- This cheque is ISSUED (payable) - your company is paying out
- Focus on VENDOR/SUPPLIER identification
- Track payment authorization and expense categorization
- Payer should be your company name
- Payee is the vendor/supplier receiving payment
"""
            elif processing_mode == "AR":
                mode_context = """
**AR Mode Context**:
- This cheque is RECEIVED (receivable) - your company is receiving payment
- Focus on CUSTOMER identification
- Track revenue recognition and deposit confirmation
- Payer is the customer making payment
- Payee should be your company name
"""
            return f"""
You are processing a cheque for Hong Kong bookkeeping. Extract data accurately for spreadsheet entry.

{mode_context}

**Context**: 
- This cheque will be recorded in accounts receivable (AR) or accounts payable (AP)
- Data must be accurate for financial reconciliation
- Support both English and Traditional Chinese text
{category_prompt_block}

**OCR Text**:
{ocr_text}

**Instructions**:
1. Fix common OCR errors: 0↔O, 1↔I↔l, 8↔B, 5↔S, 2↔Z
2. Validate financial data (amount, date, cheque number)
3. Identify Hong Kong banks (HSBC/滙豐, Hang Seng/恆生, Bank of China/中銀, Standard Chartered/渣打, etc.)
4. Determine transaction type (AR for cheques received, AP for cheques issued)
5. Prepare clean data for spreadsheet input

**Extract in JSON format**:
{{
    "cheque_number": "6-8 digit numeric code",
    "date": "YYYY-MM-DD (validate as real date)",
    "payee": "recipient/收款人 full name",
    "payer": "drawer/付款人 full name",
    "amount_numeric": "exact numeric amount (e.g., 10000.00)",
    "amount_words": "amount in words/大寫 (in Chinese if available)",
    "currency": "HKD (default if not specified)",
    "bank_name": "full bank name in English",
    "bank_code": "3-digit bank code if visible (e.g., 004 for HSBC)",
    "account_number": "account number if visible",
    "memo": "payment purpose/memo/備註",
    "transaction_type": "AR or AP",
    "account_category": "string|null",
    "cheque_type": "bearer, order, crossed, etc.",
    "errors_corrected": ["list of OCR corrections made"],
    "warnings": ["validation warnings or data quality issues"],
    "confidence_notes": "overall data quality assessment"
}}

**Validation Rules**:
✓ Amount in words must match numeric amount
✓ Date must be valid (not future date unless post-dated)
✓ Cheque number must be numeric (6-8 digits typical)
✓ Payee and Payer must be different
✓ Bank name must be a recognized HK bank
✓ Currency defaults to HKD

**Transaction Type Logic**:
- AR (Accounts Receivable): If this company is the PAYEE (receiving money)
- AP (Accounts Payable): If this company is the PAYER (paying out)
- If unclear from context, mark as "AR" (default for received cheques)

Return ONLY valid JSON. No extra text or markdown.
Return null for missing fields. Be precise with financial data.
"""
        
        elif document_type == "invoice":
            if processing_mode == "AP":
                return f"""
You are analyzing AP documents and this OCR text may contain MULTIPLE receipts/invoices mixed on one page.

Follow this strict 4-step logic:
STEP 1) Identify all merchant/vendor anchors.
STEP 2) For each anchor, assign nearest amount/date by proximity and structure.
STEP 3) Detect duplicates (same merchant + amount + date).
STEP 4) Validate completeness and confidence.
{category_prompt_block}

OCR Text:
{ocr_text}

Return ONLY valid JSON in this shape:
{{
  "multi_receipt_detected": true or false,
  "receipt_count": 2,
  "receipts": [
    {{
      "id": 1,
      "vendor": "string",
      "invoice_number": "string|null",
      "date": "YYYY-MM-DD|null",
      "currency": "HKD",
      "total_amount": "numeric|string|null",
      "account_category": "string|null",
      "is_duplicate": false,
      "duplicate_of": null,
      "confidence": 0.0
    }}
  ],
  "invoice_number": "primary receipt invoice number or null",
  "date": "primary receipt date or null",
  "vendor": "primary receipt vendor or null",
  "customer": "primary receipt customer or null",
  "currency": "HKD",
  "total_amount": "primary receipt amount or null",
  "account_category": "string|null",
  "items": [],
  "reasoning": "short 4-step summary"
}}
"""
            return f"""
Extract invoice data from this OCR text:
{category_prompt_block}

{ocr_text}

Return ONLY valid JSON with:
{{
    "invoice_number": "string",
    "date": "YYYY-MM-DD",
    "vendor": "company name",
    "customer": "company name",
    "currency": "HKD (default if not specified)",
    "total_amount": "numeric",
    "account_category": "string|null",
    "items": [
        {{"description": "string", "amount": "numeric"}}
    ]
}}
"""
        elif document_type == "receipt":
            if processing_mode == "AP":
                return f"""
You are analyzing AP receipts and this OCR text may contain MULTIPLE receipts mixed on one page.

Follow this strict 4-step logic:
STEP 1) Identify all merchant/vendor anchors.
STEP 2) For each anchor, assign nearest amount/date by proximity and structure.
STEP 3) Detect duplicates (same merchant + amount + date).
STEP 4) Validate completeness and confidence.
{category_prompt_block}

OCR Text:
{ocr_text}

Return ONLY valid JSON in this shape:
{{
  "multi_receipt_detected": true or false,
  "receipt_count": 0,
  "receipts": [
    {{
      "id": 1,
      "vendor": "string",
      "invoice_number": "string|null",
      "date": "YYYY-MM-DD|null",
      "currency": "HKD",
      "total_amount": "numeric|string|null",
      "account_category": "string|null",
      "is_duplicate": false,
      "duplicate_of": null,
      "confidence": 0.0
    }}
  ],
  "invoice_number": "primary receipt invoice number or null",
  "date": "primary receipt date or null",
  "vendor": "primary receipt vendor or null",
  "customer": "primary receipt customer or null",
  "currency": "HKD",
  "total_amount": "primary receipt amount or null",
  "account_category": "string|null",
  "items": [],
  "reasoning": "short 4-step summary"
}}
"""
            return f"""
Extract receipt data from this OCR text:
{category_prompt_block}

{ocr_text}

Return ONLY valid JSON with:
{{
    "vendor": "company name",
    "date": "YYYY-MM-DD",
    "currency": "HKD (default if not specified)",
    "total_amount": "numeric",
    "account_category": "string|null",
    "memo": "string"
}}
"""
        
        elif document_type == "bank_statement":
            return f"""
Role: You are a Hong Kong Bank Statement Data Extraction Specialist.

Objective: Extract structured transaction data from provided OCR text of bank statements and output in tab-separated values (TSV) format. Even accounts with no transactions (only balances) must be included as a single row showing the closing balance.

Input Format:
- You will receive OCR text extracted from bank statement documents.
- The OCR text may contain mixed Traditional Chinese and English, formatting issues, or recognition errors.
- Your task is to parse this raw OCR text to identify and extract transaction information.

Output Format:
- Output MUST be in TSV (tab-separated values) format with the following 15 columns:
  No.\t憑證號\t類型\t存入\t提取\t原幣結餘\t幣別\t日期\t付款人\t收款人\t銀行\t賬戶類型\t備註\tcategorise\t信心度
- First line MUST be the header row with column names exactly as above.
- Each subsequent line represents either a transaction or a no-transaction account summary.
- Do not include any additional text, explanations, or formatting outside the TSV table.

Transaction Detection Protocol:
1. Identify Bank and Account Holder:
   - Bank name: Look for bank name at the top of the statement (e.g., "中国银行(香港)").
   - Account holder: Extract from "客戶名稱" or "客户名" fields; default to company name in header (e.g., "EXAMPLE TRADING LIMITED").

2. Identify Account Sections:
   - The statement may contain multiple accounts (e.g., "港元储蓄", "外币储蓄", "港元往来"). Each account section typically has a header with the account name and account number, followed by a transaction table.
   - For each account section, determine:
        * Account Type: The account name (e.g., "港元储蓄", "港元往来", "外币储蓄").
        * Currency: Based on account name:
            - "港元" -> "HKD"
            - "外币" -> check context; if "CNY" appears, use "CNY", else default to "HKD".
   - Process each account's transactions separately. Output transactions in the order they appear, with sequential numbering across all accounts.

3. For Accounts with Transactions:
   - Follow the "Sequential Balance Method" below to extract each transaction row.
   - Output one row per transaction, with sequential numbering across all accounts.

4. For Accounts with NO Transactions:
   - If an account section contains only balance lines (e.g., "承前結餘" and "今期結餘") and no transaction rows with descriptions like "交換票", "現金交易", etc., then it has no activity.
   - Output a single row representing the account's closing balance:
        * No.: Continue sequential numbering from previous transactions.
        * 憑證號: (empty)
        * 類型: "賬戶結餘"
        * 存入: (empty)
        * 提取: (empty)
        * 原幣結餘: The closing balance from the "今期結餘" row (last numeric cell).
        * 幣別: Currency as determined.
        * 日期: The statement date from header or closing-balance date.
        * 付款人: (empty)
        * 收款人: (empty)
        * 銀行: Bank name.
        * 賬戶類型: Account type.
        * 備註: "無交易"
        * 信心度: 1.0

5. Parsing the Transaction Table (Sequential Balance Method):
   - Each account's table contains rows with transaction details. The typical column order (based on headers like "交易日期", "起息/生效日期", "交易摘要", "存入", "提取", "原幣結餘") may be misaligned due to OCR errors.
   - However, key information can be extracted by focusing on:
        * Rows that contain a date in the first cell (format YYYY/MM/DD). These are transaction rows (or balance rows like "承前結餘" and "今期結餘").
        * The last numeric cell in each such row is typically the "原幣結餘" (balance after transaction).
        * The transaction amount can be derived from the change in balance compared to the previous balance (see step 5).
   - Some rows without a date (e.g., "CDM DEP", "BIA FEE") are additional description lines belonging to the previous transaction. Combine them into that transaction's description.

4. Extract Balance and Compute Amounts (Sequential Balance Method):
   - Process all rows in order within each account.
   - Record the opening balance from the "承前結餘" row (the last numeric cell).
   - For each subsequent row that has a date and a description (not "承前結餘" or "今期結餘"), record the balance from that row (last numeric cell). The transaction amount is the absolute difference between this balance and the previous balance.
   - Determine direction:
        * If current balance > previous balance -> deposit (存入)
        * If current balance < previous balance -> withdrawal (提取)
   - The transaction date is the first cell of that row.
   - The description is the text from the description column(s) plus any following non-date rows.
   - IMPORTANT: direction for 存入/提取 MUST be determined by balance delta, not by 付款人/收款人 identity.

6. Field-by-Field Extraction:
   a. No.: Sequential number starting from 1 for each transaction across all accounts.
   b. 憑證號: Leave empty (not provided).
   c. 類型: Based on description and direction:
        - If deposit:
            * Description contains "交換票" -> "交換票存入"
            * Description contains "現金交易" -> "現金存入"
            * Otherwise -> "其他存入"
        - If withdrawal:
            * Description contains "退票" -> "退票"
            * Description contains "銀行費用" or "BIA FEE" -> "銀行費用"
            * Description contains "現金交易" -> "現金提取"
            * Description contains "交換票" -> "交換票提取"
            * Otherwise -> "其他提取"
   d. 存入: If deposit, put the transaction amount (positive number, remove commas). Otherwise blank.
   e. 提取: If withdrawal, put the transaction amount (positive number, remove commas). Otherwise blank.
   f. 原幣結餘: The balance after this transaction, extracted from the row (last numeric cell). Remove commas, keep as number.
   g. 幣別: Currency determined from account (as per step 2).
   h. 日期: Transaction date in YYYY-MM-DD format (convert from YYYY/MM/DD).
   i. 付款人:
        - If deposit: "Unknown"
        - If withdrawal: Account holder name
        - If bank fee: Account holder name
   j. 收款人:
        - If deposit: Account holder name
        - If withdrawal: "Unknown"
        - If bank fee: Bank name
   - Note: 付款人/收款人 are contextual fields only and MUST NOT override balance-based direction.
   k. 銀行: Bank name from header.
   l. 賬戶類型: Account type (e.g., "港元储蓄", "港元往来", "外币储蓄") as identified in step 2.
   m. 備註: Full transaction description (combine all description parts, separated by space).
   n. categorise:
        - If description indicates bank fee/service charge -> "5080 Bank Fee"
        - If description indicates interest paid/charged -> "5090 Interest Paid"
        - If description indicates interest received -> "4030 Interest Received"
        - Otherwise leave empty
   o. 信心度: Confidence score (0.0 to 1.0, one decimal):
        - 1.0: Complete transaction with clear balance difference.
        - 0.9: Minor OCR issues but amounts derived correctly.
        - 0.7: Some ambiguity in description or balance.
        - 0.5: Incomplete transaction.

7. OCR Error Handling:
   - Remove commas from numbers (e.g., "480,000.00" -> 480000.00).
   - Correct common OCR errors using context.
   - If a row has multiple numeric cells, the last one is the balance; any preceding numeric cells are ignored (use balance difference for amount).
   - For opening balance, take the last numeric cell.

8. IMPORTANT: Your response MUST be ONLY the TSV table with header and data rows, no additional text.

Now, process the following OCR text from a bank statement:
{ocr_text}
"""
        
        elif document_type == "bank_statement_page":
            bank = metadata.get('bank', 'UNKNOWN')
            page_num = metadata.get('page_num', 1)
            
            return f"""
Role: You are a Hong Kong Bank Statement Data Extraction Specialist.

Context:
- Bank hint: {bank}
- Page number: {page_num}
- This page contains MULTIPLE transactions in a table format. Extract ALL transactions on this page.

Output Format (TSV only):
No.\t憑證號\t類型\t存入\t提取\t原幣結餘\t幣別\t日期\t付款人\t收款人\t銀行\t賬戶類型\t備註\tcategorise\t信心度

Rules:
- Parse HTML-like/table-like OCR text and preserve row meaning.
- Merge multi-line/multi-row description fragments into one transaction note.
- Skip balance/summary rows such as 承前結餘, 今期結餘, 合計.
- Prefer BOC-style mapping when the structure matches:
  date | value date | description | 存入 | 提取 | 原幣結餘
- Determine 存入/提取 by sequential 原幣結餘 math first; do NOT infer direction from 付款人/收款人.
- Infer 賬戶類型 from page header keywords (e.g., 港元储蓄 / 港元往来 / 外币储蓄) if not explicit per row.
- If the page/account section has no transaction rows and only balance lines, output one summary row:
  類型=賬戶結餘, 原幣結餘=closing balance, 備註=無交易, 存入/提取 empty, 信心度=1.0.
- Normalize date to YYYY-MM-DD.
- Normalize amount and balance to plain decimal without thousand separators.
- Set categorise only for bank fee/interest transactions; otherwise keep it empty.
- Output ONLY TSV rows with the header.

OCR Text:
{ocr_text}
"""
        
        else:
            return f"""
Extract key information from this document:

{ocr_text}

Return JSON with relevant fields based on document content.
"""
    
    def _parse_ai_response(self, response: str) -> Dict[str, Any]:
        """Parse AI response to extract JSON data"""
        try:
            # Try to find JSON in response
            start_idx = response.find('{')
            end_idx = response.rfind('}') + 1
            
            if start_idx >= 0 and end_idx > start_idx:
                json_str = response[start_idx:end_idx]
                return json.loads(json_str)
            
            logger.warning("No JSON found in AI response")
            return {"error": "Failed to parse AI response", "raw_text": response}
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return {"error": "Invalid JSON from AI", "raw_text": response}
    
    def _calculate_confidence(self, extracted_data: Dict[str, Any]) -> float:
        """Calculate confidence score based on extracted data quality for HK bookkeeping"""
        confidence = 1.0
        
        # Check for missing critical fields (HK bookkeeping essentials)
        critical_fields = ["cheque_number", "date", "amount_numeric", "payee", "payer"]
        missing_fields = [f for f in critical_fields if not extracted_data.get(f)]
        
        if missing_fields:
            confidence -= 0.1 * len(missing_fields)
        
        # Check for missing important fields (nice to have)
        important_fields = ["bank_name", "transaction_type"]
        missing_important = [f for f in important_fields if not extracted_data.get(f)]
        
        if missing_important:
            confidence -= 0.05 * len(missing_important)
        
        # Check for warnings
        if extracted_data.get("warnings"):
            confidence -= 0.03 * len(extracted_data["warnings"])
        
        # Check for validation errors
        if extracted_data.get("error"):
            confidence -= 0.3
        
        # Bonus for having bank code (indicates good OCR quality)
        if extracted_data.get("bank_code"):
            confidence += 0.05
        
        # Bonus for having transaction type classified
        if extracted_data.get("transaction_type") in ["AR", "AP"]:
            confidence += 0.05
        
        return max(0.0, min(1.0, confidence))
    
    def _fallback_extraction(self, ocr_result: OcrResult | str, warning: Optional[str] = None) -> Dict[str, Any]:
        """Fallback extraction without AI (rule-based)"""
        if isinstance(ocr_result, str):
            raw_text = ocr_result
            lines = []
        else:
            raw_text = "\n".join([line.text for line in ocr_result.lines])
            lines = [
                {
                    "text": line.text,
                    "confidence": line.confidence
                }
                for line in ocr_result.lines
            ]
        payload = {
            "ai_processed": False,
            "raw_ocr_text": raw_text,
            "lines": lines,
            "warning": "AI post-processing not available. Using raw OCR results."
        }
        if warning:
            payload["warning"] = warning
        return payload


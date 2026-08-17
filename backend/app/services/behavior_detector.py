"""
Behavior Detector — learns user patterns from table edits and proposes
CompanyRules automatically.

How it works
------------
1. When the user edits a table cell via PATCHES (e.g. changes category for a
   vendor), record_edit() stores that observation in RAM per session.
2. After N observations of the same (vendor, field, value) pattern,
   get_pending_suggestions() returns a suggestion the AI can present to the
   user ("I noticed you always assign Vendor X to category Y — save rule?").
3. When the user confirms ("yes" / "記住"), create_rule_from_pattern() writes a
   CompanyRule to the database.
4. Explicit "remember this rule" / "記住這個規則" commands are detected by
   parse_remember_command() and routed directly to create_rule_from_pattern().
"""
from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Minimum times the same pattern must be observed before AI suggests saving it
_PATTERN_THRESHOLD = 2

# Patchable fields that are useful to persist as rules
_RULE_FIELDS = {"category", "account_code", "transaction_type"}

# Regex patterns that trigger "remember this rule" in both English and Chinese.
# Covers natural language like "remember this kind of...", "always use X for this type", etc.
_REMEMBER_PATTERNS = [
    # Original exact phrases
    re.compile(r"remember\s+this\s+rule", re.IGNORECASE),
    re.compile(r"save\s+this\s+rule", re.IGNORECASE),
    re.compile(r"keep\s+this\s+rule", re.IGNORECASE),
    # "remember this [kind/type/sort] of ..."
    re.compile(r"remember\s+this\s+(kind|type|sort|category|class)\s+of", re.IGNORECASE),
    # "remember this" followed by account/invoice/vendor/document context
    re.compile(r"remember\s+this\s+(invoice|account|vendor|document|transaction|receipt|category)", re.IGNORECASE),
    # "always [use/assign/map/set] X [for/to/as] ..."
    re.compile(r"always\s+(use|assign|map|set|apply|classify|categorize)\b", re.IGNORECASE),
    # "save this [as a rule / for future / preference]"
    re.compile(r"save\s+this(\s+(as\s+a?\s*rule|for\s+future|preference|setting))?", re.IGNORECASE),
    # "make this a rule"
    re.compile(r"make\s+this\s+a?\s*rule", re.IGNORECASE),
    # "set [this/it] as [default/rule/standard]"
    re.compile(r"set\s+(this|it)\s+as\s+(default|rule|standard|the\s+default)", re.IGNORECASE),
    # "use X for this type" / "use sales for invoices like this"
    re.compile(r"use\s+\S+\s+for\s+(this|these)\s+(type|kind|sort|category)", re.IGNORECASE),
    # "this [type/kind] [of invoice/document] [should/will/must] be ..."
    re.compile(r"this\s+(type|kind|sort)\s+of\s+\w+\s+(should|will|must|needs?\s+to)\s+be\b", re.IGNORECASE),
    # Chinese: 記住 / 儲存 / 記錄 (with or without 規則/這個)
    re.compile(r"記住(這個|這種|這類|此類|此種)?規則?", re.IGNORECASE),
    re.compile(r"儲存(這個|這種|這類|此類|此種)?規則?", re.IGNORECASE),
    re.compile(r"記錄(這個|這種|這類|此類)?規則", re.IGNORECASE),
    # Chinese: 以後/以後都/永遠 + 用/設定/分類
    re.compile(r"以後(都)?(用|設定|分類|歸類|記為)", re.IGNORECASE),
    re.compile(r"永遠(都)?(用|設定|分類|歸類|記為)", re.IGNORECASE),
    # Chinese: 這種/這類 [發票/單據/文件] [應該/都] 是/記為
    re.compile(r"這(種|類|樣)(發票|單據|文件|帳單|收據|費用)?(應該|都|要|需要)?(是|記為|歸為|分類)", re.IGNORECASE),
]


class BehaviorDetector:
    """
    Per-session behavior tracker.  Holds edit observations in RAM; persists
    rules to DB on explicit confirmation only.

    Key structure:
      _observations[session_id][(vendor, field, value)] = count
      _last_edit[session_id] = {vendor, field, value, ...} for "remember this"
      _pending_suggestions[session_id] = list of suggestion dicts
    """

    def __init__(self) -> None:
        self._observations: dict[str, dict[tuple, int]] = defaultdict(
            lambda: defaultdict(int)
        )
        self._last_edit: dict[str, dict[str, Any]] = {}
        self._pending_suggestions: dict[str, list[dict]] = defaultdict(list)

    # ── Public API ────────────────────────────────────────────────────────────

    def record_edit(
        self,
        session_id: str,
        patch: dict[str, Any],
        transaction_context: dict[str, Any] | None = None,
    ) -> None:
        """
        Record an observed field edit from a PATCHES item.

        patch keys: id_number, field, value
        transaction_context: the transaction row the patch applies to
          (should contain 'payer', 'payee', 'vendor' or similar)
        """
        field = patch.get("field", "")
        value = patch.get("value", "")
        if field not in _RULE_FIELDS or not value:
            return

        vendor = _extract_vendor(patch, transaction_context)
        if not vendor:
            return

        key = (vendor.lower(), field, str(value))
        self._observations[session_id][key] += 1
        self._last_edit[session_id] = {
            "vendor": vendor,
            "field": field,
            "value": str(value),
            "id_number": patch.get("id_number", ""),
        }

        count = self._observations[session_id][key]
        if count >= _PATTERN_THRESHOLD:
            self._maybe_add_suggestion(session_id, vendor, field, str(value), count)

    def get_pending_suggestions(self, session_id: str) -> list[dict]:
        """
        Return and clear the list of pending suggestions for this session.
        The caller (ai_chat.py) injects these into the system prompt so the
        AI asks the user for confirmation.
        """
        suggestions = list(self._pending_suggestions.get(session_id, []))
        self._pending_suggestions[session_id] = []
        return suggestions

    def parse_remember_command(self, session_id: str, user_message: str) -> dict | None:
        """
        Check if the user message is an explicit "remember this rule" command.
        Returns the last_edit context (from prior table edits) if available,
        otherwise attempts to extract field/value from the message text itself.
        Returns None if no pattern matches.
        """
        matched = any(p.search(user_message) for p in _REMEMBER_PATTERNS)
        if not matched:
            return None

        # Prefer context from a recent table edit
        last = self._last_edit.get(session_id)
        if last and last.get("vendor") and last.get("field") and last.get("value"):
            return last

        # Fall back: try to extract field + value from the message text itself
        extracted = _extract_rule_from_text(user_message)
        if extracted:
            return extracted

        # Matched the pattern but no context available — return empty dict so
        # the caller knows a remember command was issued even without data
        return {}

    def confirm_suggestion(
        self,
        session_id: str,
        suggestion_key: tuple,
    ) -> dict | None:
        """
        Called when the user confirms a pending suggestion.
        Returns the suggestion dict for rule creation, or None.
        """
        vendor, field, value = suggestion_key
        last = self._last_edit.get(session_id, {})
        if last.get("vendor", "").lower() == vendor and last.get("field") == field:
            return last
        return None

    def create_rule_from_pattern(
        self,
        company_id: str,
        vendor: str,
        field: str,
        value: str,
        db: Session,
        created_by: str = "ai_behavior",
        keyword_hint: str | None = None,
        document_type: str | None = None,
        mode: str = "AR",
    ) -> bool:
        """
        Write a learned rule to the Company Rule Memory MD (not to the old DB rules table).
        Returns True if saved/updated, False on failure.
        """
        try:
            from app.services.rule_memory_parser import (
                append_vendor_rule,
                append_keyword_rule,
                check_dedup,
            )
            from app.models.rule_memory import CompanyRuleMemory, VALID_MODES
            import uuid as _uuid

            m = (mode or "AR").upper()
            if m not in VALID_MODES:
                m = "AR"

            row = db.query(CompanyRuleMemory).filter(
                CompanyRuleMemory.company_id == company_id,
                CompanyRuleMemory.mode == m,
            ).first()

            if row is None:
                # Create the memory row with a starter template
                from app.services.rule_memory_templates import get_starter_template
                row = CompanyRuleMemory(
                    id=str(_uuid.uuid4()),
                    company_id=company_id,
                    mode=m,
                    content=get_starter_template(m),
                    version=1,
                    updated_by_type="system",
                    is_active=True,
                )
                db.add(row)
                db.flush()

            md = row.content or ""

            # Skip if identical rule already exists
            if check_dedup(md, vendor or None, field, value):
                logger.info("[Behavior] Rule already exists for vendor=%s field=%s", vendor, field)
                return True

            if vendor and vendor.strip():
                md = append_vendor_rule(md, vendor.strip(), field, value)
            elif keyword_hint:
                md = append_keyword_rule(md, [keyword_hint.strip().lower()], field, value)
            else:
                # Fall back to vendor/keyword based on available data
                md = append_keyword_rule(md, [vendor or field], field, value)

            row.content = md
            row.version = (row.version or 1) + 1
            row.updated_by_type = "ai"
            db.commit()
            logger.info(
                "[Behavior] Saved rule to memory company=%s mode=%s vendor=%s field=%s value=%s",
                company_id, m, vendor, field, value,
            )
            return True
        except Exception as exc:
            logger.error("[Behavior] Failed to save rule to memory: %s", exc)
            try:
                db.rollback()
            except Exception:
                pass
            return False

    # ── Private helpers ───────────────────────────────────────────────────────

    def _maybe_add_suggestion(
        self,
        session_id: str,
        vendor: str,
        field: str,
        value: str,
        count: int,
    ) -> None:
        suggestions = self._pending_suggestions[session_id]
        for s in suggestions:
            if s["vendor"] == vendor and s["field"] == field:
                return  # already queued
        suggestions.append(
            {
                "vendor": vendor,
                "field": field,
                "value": value,
                "count": count,
                "key": (vendor.lower(), field, value),
            }
        )
        logger.info(
            "[Behavior] New suggestion queued: vendor=%s field=%s value=%s (seen %dx)",
            vendor,
            field,
            value,
            count,
        )


def _extract_rule_from_text(message: str) -> dict | None:
    """
    Attempt to extract field + value from a natural-language remember command.

    Examples handled:
      "remember this kind of invoice account code will be sales"
        → field=account_code, value=sales
      "always use 4010 for this type of invoice"
        → field=account_code, value=4010
      "this type of expense should be classified as office supplies"
        → field=category, value=office supplies
    """
    msg = message.lower()

    # ── account code / account number ────────────────────────────────────────
    # "account code will be X" / "account code is X" / "account code: X"
    m = re.search(
        r"account\s*(code|number|no\.?)\s*(will\s+be|is|=|:)?\s*(['\"]?)(\w[\w\s\-]*?)\3"
        r"(?:\s|$|,|\.|;)",
        msg,
    )
    if m:
        return {"vendor": "", "field": "account_code", "value": m.group(4).strip()}

    # "use [code] for" pattern: "use 4010 for invoices"
    m = re.search(r"use\s+([0-9]{3,6})\s+for\b", msg)
    if m:
        return {"vendor": "", "field": "account_code", "value": m.group(1).strip()}

    # ── category / classification ─────────────────────────────────────────────
    # "category will be X" / "classified as X" / "categorize as X"
    m = re.search(
        r"(category|categori[sz]e?\s+as|classified\s+as|classify\s+as|map\s+to"
        r"|should\s+be|will\s+be|记为|歸為|分類為)\s+(['\"]?)([\w][\w\s\-\/&]{1,40})\2",
        msg,
    )
    if m:
        return {"vendor": "", "field": "category", "value": m.group(3).strip()}

    # ── transaction type ──────────────────────────────────────────────────────
    m = re.search(
        r"transaction\s+type\s+(will\s+be|is|=|:)?\s*(['\"]?)([\w][\w\s\-]{1,30})\2",
        msg,
    )
    if m:
        return {"vendor": "", "field": "transaction_type", "value": m.group(3).strip()}

    return None


def _extract_vendor(
    patch: dict[str, Any],
    context: dict[str, Any] | None,
) -> str:
    """
    Extract the most useful vendor/payer identifier from available data.
    Priority: context.payee > context.payer > context.vendor > patch.id_number
    """
    if context:
        for key in ("payee", "payer", "vendor", "counterparty"):
            val = context.get(key, "")
            if val and str(val).strip() and str(val).lower() not in ("unknown", "n/a", ""):
                return str(val).strip()
    # Fall back to id_number as a last resort identifier
    return ""


# Module-level singleton
behavior_detector = BehaviorDetector()

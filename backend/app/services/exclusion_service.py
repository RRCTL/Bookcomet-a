"""
Exclusion Service
=================
Applies per-company exclusion rules to OCR-extracted rows.

When a row matches an exclusion rule it is flagged:
  row["needs_manual_review"] = True
  row["exclusion_reasons"]   = ["Reason text ...", ...]

This happens AFTER the 3-tier rule engine so field classification still
runs — the row is classified AND flagged for human review.

The service also updates hit counts on the matched rules (fire-and-forget;
errors are swallowed so they never break the OCR pipeline).
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# Fuzzy match threshold reused from rule_memory_parser
_VENDOR_FUZZY_THRESHOLD = 82


def apply_exclusion_rules_to_rows(
    rows: list[dict[str, Any]],
    exclusion_rules: list,  # list of ExclusionRule ORM objects
    ocr_text: str = "",
    mode: str = "",
    db=None,
) -> list[dict[str, Any]]:
    """
    Flag rows that match any active exclusion rule.

    Args:
        rows:             OCR-extracted transaction rows.
        exclusion_rules:  Active ExclusionRule ORM objects for this company.
        ocr_text:         Full OCR text of the document (for keyword search).
        mode:             Current OCR mode (AR/AP/BANK/OTHER).
        db:               SQLAlchemy session for updating hit counts (optional).

    Returns:
        The (possibly mutated) rows list.
    """
    if not exclusion_rules or not rows:
        return rows

    # Filter rules to those applicable to the current mode
    active_rules = [
        r for r in exclusion_rules
        if r.is_active and _rule_applies_to_mode(r, mode)
    ]
    if not active_rules:
        return rows

    hit_rule_ids: set[str] = set()

    for row in rows:
        reasons: list[str] = []

        for rule in active_rules:
            matched_reasons = check_row_exclusions(row, [rule], ocr_text, mode)
            if matched_reasons:
                reasons.extend(mr["reason"] for mr in matched_reasons)
                hit_rule_ids.add(rule.id)

        if reasons:
            row["needs_manual_review"] = True
            row["exclusion_reasons"] = reasons

    # Update hit counts (non-blocking, errors ignored)
    if db and hit_rule_ids:
        _update_hit_counts(db, hit_rule_ids)

    return rows


def check_row_exclusions(
    row: dict[str, Any],
    rules: list,
    ocr_text: str = "",
    mode: str = "",
) -> list[dict]:
    """
    Return a list of matched rule dicts (id, pattern, reason) for a single row.
    Used by the /test endpoint and apply_exclusion_rules_to_rows.
    """
    matched = []
    full_text = _row_to_text(row).lower() + " " + (ocr_text or "").lower()

    for rule in rules:
        if not rule.is_active:
            continue
        if not _rule_applies_to_mode(rule, mode):
            continue

        hit = False
        if rule.pattern_type == "keyword":
            hit = rule.pattern.lower() in full_text

        elif rule.pattern_type == "vendor":
            row_vendor = _extract_row_vendor(row)
            hit = _vendor_matches(row_vendor, rule.pattern)

        elif rule.pattern_type == "amount":
            try:
                threshold = float(rule.pattern)
                row_amount = row.get("amount")
                if row_amount is not None:
                    hit = abs(float(row_amount)) >= threshold
            except (ValueError, TypeError):
                pass

        if hit:
            matched.append({
                "rule_id": rule.id,
                "pattern": rule.pattern,
                "pattern_type": rule.pattern_type,
                "reason": rule.reason or f"Exclusion rule matched: {rule.pattern}",
            })

    return matched


# ── Private helpers ───────────────────────────────────────────────────────────

def _rule_applies_to_mode(rule, mode: str) -> bool:
    """Return True if the rule applies to the given OCR mode."""
    if not rule.modes or not rule.modes.strip():
        return True  # empty = all modes
    rule_modes = [m.strip().upper() for m in rule.modes.split(",") if m.strip()]
    return not mode or mode.upper() in rule_modes


def _row_to_text(row: dict) -> str:
    parts = []
    skip = {"rule_applied", "rule_conflicts", "needs_manual_review", "exclusion_reasons"}
    for k, v in row.items():
        if v and k not in skip:
            parts.append(str(v))
    return " ".join(parts)


def _extract_row_vendor(row: dict) -> str:
    for key in ("payer", "payee", "vendor", "counterparty", "company"):
        val = row.get(key)
        if val and str(val).strip() and str(val).lower() not in ("unknown", "n/a", ""):
            return str(val).strip()
    return ""


def _vendor_matches(row_vendor: str, rule_vendor: str) -> bool:
    if not row_vendor or not rule_vendor:
        return False
    rv = row_vendor.lower().strip()
    rr = rule_vendor.lower().strip()
    if rv == rr or rr in rv or rv in rr:
        return True
    try:
        from difflib import SequenceMatcher
        return SequenceMatcher(None, rv, rr).ratio() * 100 >= _VENDOR_FUZZY_THRESHOLD
    except Exception:
        return False


def _update_hit_counts(db, rule_ids: set[str]) -> None:
    """Increment hit_count and update last_hit_at for matched rules."""
    try:
        from app.models.exclusion_rule import ExclusionRule
        now = datetime.utcnow()
        for rule_id in rule_ids:
            rule = db.query(ExclusionRule).filter(ExclusionRule.id == rule_id).first()
            if rule:
                rule.hit_count = (rule.hit_count or 0) + 1
                rule.last_hit_at = now
        db.commit()
    except Exception as exc:
        logger.warning("[ExclusionService] Failed to update hit counts: %s", exc)
        try:
            db.rollback()
        except Exception:
            pass

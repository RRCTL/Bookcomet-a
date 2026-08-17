"""
Rule Memory Parser
==================
Parses the structured Markdown stored in CompanyRuleMemory.content and applies
rules to extracted OCR rows using a 3-tier priority system:

  Priority 1 (highest) — Vendor-Specific Rules (## Vendor-Specific Rules)
  Priority 2           — Keyword Rules          (## Keyword Rules)
  Priority 3 (lowest)  — Document Defaults      (## Document Defaults)

Conflict policy: if a rule would set a field that already has an extracted
value, the conflict is flagged on the row rather than silently overwriting.

Section: ## AI Behaviour Instructions  — returned separately for Stage 1 VLM injection.
"""
from __future__ import annotations

import re
from typing import Any

# ── Section name constants (must match MD template exactly) ──────────────────

_SEC_AI_INSTRUCTIONS = "AI Behaviour Instructions"
_SEC_DEFAULTS = "Document Defaults"
_SEC_KEYWORDS = "Keyword Rules"
_SEC_VENDORS = "Vendor-Specific Rules"

_ALL_SECTIONS = [_SEC_AI_INSTRUCTIONS, _SEC_DEFAULTS, _SEC_KEYWORDS, _SEC_VENDORS]

# Fuzzy vendor match threshold (0-100)
_VENDOR_FUZZY_THRESHOLD = 82


# ── Public helpers ────────────────────────────────────────────────────────────

def extract_ai_instructions(md_content: str) -> str:
    """
    Return only the ## AI Behaviour Instructions section text.
    Used for Stage 1 VLM prompt injection (layout/format hints only).
    """
    return _get_section(md_content, _SEC_AI_INSTRUCTIONS)


def parse_rules(md_content: str) -> dict[str, list[dict]]:
    """
    Parse all rule sections from the MD and return structured dicts.

    Returns:
        {
            "defaults":  [{"field": str, "value": str}, ...],
            "keywords":  [{"keywords": [str], "field": str, "value": str}, ...],
            "vendors":   [{"vendor": str, "fields": {field: value, ...}}, ...],
        }
    """
    return {
        "defaults": _parse_defaults(_get_section(md_content, _SEC_DEFAULTS)),
        "keywords": _parse_keyword_rules(_get_section(md_content, _SEC_KEYWORDS)),
        "vendors":  _parse_vendor_rules(_get_section(md_content, _SEC_VENDORS)),
    }


def apply_rules_to_rows(
    rows: list[dict[str, Any]],
    md_content: str,
    ocr_text: str = "",
) -> list[dict[str, Any]]:
    """
    Apply 3-tier rules from md_content to OCR-extracted rows.

    Rules only fill EMPTY fields.  If a rule would change a non-empty field
    a 'rule_conflicts' list is appended to the row for the frontend to display.

    Returns the (possibly mutated) rows list.
    """
    if not md_content or not rows:
        return rows

    parsed = parse_rules(md_content)
    full_text = ocr_text.lower()

    for row in rows:
        conflicts: list[dict] = row.get("rule_conflicts") or []

        # ── Priority 3: Document Defaults (fill empty only) ───────────────
        for rule in parsed["defaults"]:
            field = rule["field"]
            value = rule["value"]
            current = row.get(field)
            if _is_empty(current):
                row[field] = value
                row.setdefault("rule_applied", []).append(
                    {"priority": 3, "source": "Document Defaults", "field": field, "value": value}
                )

        # ── Priority 2: Keyword Rules ─────────────────────────────────────
        row_text = _row_to_text(row).lower() + " " + full_text
        for rule in parsed["keywords"]:
            if not any(kw in row_text for kw in rule["keywords"]):
                continue
            field = rule["field"]
            value = rule["value"]
            current = row.get(field)
            if _is_empty(current):
                row[field] = value
                row.setdefault("rule_applied", []).append(
                    {"priority": 2, "source": "Keyword Rules",
                     "matched_keywords": rule["keywords"], "field": field, "value": value}
                )
            elif str(current).strip().lower() != str(value).strip().lower():
                conflicts.append({
                    "field": field,
                    "extracted_value": str(current),
                    "rule_value": str(value),
                    "rule_source": f"Keyword: {rule['keywords'][0]}",
                })

        # ── Priority 1: Vendor-Specific Rules ─────────────────────────────
        row_vendor = _extract_row_vendor(row)
        for vr in parsed["vendors"]:
            if not _vendor_matches(row_vendor, vr["vendor"]):
                continue
            for field, value in vr["fields"].items():
                current = row.get(field)
                if _is_empty(current):
                    row[field] = value
                    row.setdefault("rule_applied", []).append(
                        {"priority": 1, "source": "Vendor-Specific Rules",
                         "vendor": vr["vendor"], "field": field, "value": value}
                    )
                elif str(current).strip().lower() != str(value).strip().lower():
                    conflicts.append({
                        "field": field,
                        "extracted_value": str(current),
                        "rule_value": str(value),
                        "rule_source": f"Vendor: {vr['vendor']}",
                    })

        if conflicts:
            row["rule_conflicts"] = conflicts

    return rows


# ── Section helpers ───────────────────────────────────────────────────────────

def _get_section(md: str, section_name: str) -> str:
    """Extract text content of a ## section (stops at next ## heading or EOF)."""
    pattern = re.compile(
        r"##\s+" + re.escape(section_name) + r"\s*\n(.*?)(?=\n##\s+|\Z)",
        re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(md)
    if m:
        return m.group(1).strip()
    return ""


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_defaults(section: str) -> list[dict]:
    """
    Parse lines like:
      - Default Currency: HKD
      - Currency: HKD
    """
    rules = []
    for line in section.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line or line.startswith("#"):
            continue
        # Strip leading "Default " prefix if present
        line = re.sub(r"^Default\s+", "", line, flags=re.IGNORECASE)
        if ":" not in line:
            continue
        field_raw, _, value_raw = line.partition(":")
        field = _normalise_field(field_raw.strip())
        value = value_raw.strip().split("(")[0].strip()  # strip "(explanation)"
        if field and value:
            rules.append({"field": field, "value": value})
    return rules


def _parse_keyword_rules(section: str) -> list[dict]:
    """
    Parse lines like:
      - "consulting", "顧問" → Account: 4003 (Consulting Income)
      - freight, delivery → Account: 5010
    """
    rules = []
    for line in section.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line or line.startswith("#") or "→" not in line:
            continue
        # Skip comment/instruction lines wrapped in *(...)*
        if line.startswith("*(") or line.startswith("*Format") or line.startswith("*format"):
            continue
        left, _, right = line.partition("→")
        # Extract quoted or unquoted keywords
        keywords = re.findall(r'["\u201c\u201d]([^"\u201c\u201d]+)["\u201c\u201d]', left)
        if not keywords:
            keywords = [k.strip().lower() for k in re.split(r"[,/|]+", left) if k.strip()]
        else:
            keywords = [k.strip().lower() for k in keywords]

        if ":" not in right:
            continue
        field_raw, _, value_raw = right.strip().partition(":")
        field = _normalise_field(field_raw.strip())
        value = value_raw.strip().split("(")[0].strip()
        if keywords and field and value:
            rules.append({"keywords": keywords, "field": field, "value": value})
    return rules


def _parse_vendor_rules(section: str) -> list[dict]:
    """
    Parse lines like:
      - ABC Corporation → Account: 4001 (Sales Revenue), Tax: ST
      - ABC Corporation → Account: 4001, Tax: ST, Currency: HKD
    """
    rules = []
    for line in section.splitlines():
        line = line.strip().lstrip("-").strip()
        if not line or line.startswith("#") or "→" not in line:
            continue
        # Skip comment/instruction lines wrapped in *(...)*
        if line.startswith("*(") or line.startswith("*Format") or line.startswith("*format"):
            continue
        vendor_raw, _, right = line.partition("→")
        vendor = vendor_raw.strip()
        if not vendor:
            continue
        fields: dict[str, str] = {}
        # Split by comma, parse "Field: Value" pairs
        for part in right.split(","):
            part = part.strip()
            if ":" not in part:
                continue
            field_raw, _, value_raw = part.partition(":")
            field = _normalise_field(field_raw.strip())
            value = value_raw.strip().split("(")[0].strip()
            if field and value:
                fields[field] = value
        if vendor and fields:
            rules.append({"vendor": vendor, "fields": fields})
    return rules


# ── Field name normalisation ──────────────────────────────────────────────────

_FIELD_ALIASES: dict[str, str] = {
    "account": "account_code",
    "account code": "account_code",
    "account no": "account_code",
    "gl account": "account_code",
    "gl": "account_code",
    "tax": "tax_code",
    "tax code": "tax_code",
    "vat": "tax_code",
    "curr": "currency",
    "ccy": "currency",
    "type": "transaction_type",
    "transaction type": "transaction_type",
    "cat": "category",
    "memo": "memo",
    "note": "memo",
    "notes": "memo",
    "payment terms": "payment_terms",
    "terms": "payment_terms",
}


def _normalise_field(raw: str) -> str:
    key = raw.strip().lower().rstrip(":")
    return _FIELD_ALIASES.get(key, key.replace(" ", "_"))


# ── Matching helpers ──────────────────────────────────────────────────────────

def _is_empty(value: Any) -> bool:
    return value is None or str(value).strip() in ("", "None", "null", "N/A", "n/a", "—", "-")


def _row_to_text(row: dict) -> str:
    """Flatten row values to a searchable string."""
    parts = []
    for k, v in row.items():
        if v and k not in ("rule_applied", "rule_conflicts", "id_number"):
            parts.append(str(v))
    return " ".join(parts)


def _extract_row_vendor(row: dict) -> str:
    for key in ("payer", "payee", "vendor", "counterparty", "company"):
        val = row.get(key)
        if val and str(val).strip() and str(val).lower() not in ("unknown", "n/a", ""):
            return str(val).strip()
    return ""


def _vendor_matches(row_vendor: str, rule_vendor: str) -> bool:
    """
    Case-insensitive fuzzy match.
    Simple: check if one contains the other, or ratio ≥ threshold.
    """
    if not row_vendor or not rule_vendor:
        return False
    rv = row_vendor.lower().strip()
    rr = rule_vendor.lower().strip()
    if rv == rr or rr in rv or rv in rr:
        return True
    # Simple character overlap ratio
    try:
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, rv, rr).ratio() * 100
        return ratio >= _VENDOR_FUZZY_THRESHOLD
    except Exception:
        return False


# ── MD template builder ───────────────────────────────────────────────────────

def build_empty_md(mode: str, company_name: str = "") -> str:
    """Return a blank-but-structured MD template for a new company mode."""
    header = f"# {mode} Rules Memory"
    if company_name:
        header += f" — {company_name}"
    return f"""{header}

## AI Behaviour Instructions
*(Hints for the OCR image reader — vendor-specific layout knowledge)*

## Document Defaults
*(Lowest priority: fills empty fields when no other rule matches)*
- Currency: HKD
- Tax Code: ST

## Keyword Rules
*(Medium priority: applied when keyword is found anywhere in the document)*
*(Format: - "keyword1", "keyword2" → Field: value)*

## Vendor-Specific Rules
*(Highest priority: applied only when this vendor is matched)*
*(Format: - Vendor Name → Account: 4001, Tax: ST)*
"""


def append_vendor_rule(md_content: str, vendor: str, field: str, value: str) -> str:
    """
    Add or update a vendor rule in the ## Vendor-Specific Rules section.
    If the vendor already exists, update/add the field.  Otherwise append a new line.
    """
    section_header = "## Vendor-Specific Rules"
    if section_header not in md_content:
        md_content += f"\n{section_header}\n"

    lines = md_content.splitlines()
    in_section = False
    vendor_line_idx = -1

    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped == section_header
        if in_section and stripped.startswith("- ") and "→" in stripped:
            vendor_part = stripped[2:].split("→")[0].strip()
            if _vendor_matches(vendor_part, vendor):
                vendor_line_idx = i
                break

    if vendor_line_idx >= 0:
        # Update existing vendor line — add/replace the field
        existing = lines[vendor_line_idx]
        vpart, _, rpart = existing.partition("→")
        # Parse existing fields
        existing_fields: dict[str, str] = {}
        for part in rpart.split(","):
            part = part.strip()
            if ":" in part:
                f_raw, _, v_raw = part.partition(":")
                fn = _normalise_field(f_raw.strip())
                existing_fields[fn] = v_raw.strip().split("(")[0].strip()
        existing_fields[_normalise_field(field)] = value
        new_rpart = ", ".join(
            f"{_denormalise_field(f)}: {v}" for f, v in existing_fields.items()
        )
        lines[vendor_line_idx] = f"{vpart}→ {new_rpart}"
        return "\n".join(lines)
    else:
        # Find the end of the vendor section and append
        field_norm = _normalise_field(field)
        new_line = f"- {vendor} → {_denormalise_field(field_norm)}: {value}"
        # Insert before the next ## or at end of file
        insert_at = len(lines)
        in_section = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped == section_header:
                in_section = True
                continue
            if in_section and stripped.startswith("## "):
                insert_at = i
                break
        lines.insert(insert_at, new_line)
        return "\n".join(lines)


def append_keyword_rule(md_content: str, keywords: list[str], field: str, value: str) -> str:
    """Append a keyword rule to ## Keyword Rules section."""
    section_header = "## Keyword Rules"
    if section_header not in md_content:
        md_content += f"\n{section_header}\n"

    kw_str = ", ".join(f'"{k}"' for k in keywords)
    field_norm = _normalise_field(field)
    new_line = f"- {kw_str} → {_denormalise_field(field_norm)}: {value}"

    lines = md_content.splitlines()
    insert_at = len(lines)
    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == section_header:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            insert_at = i
            break
    lines.insert(insert_at, new_line)
    return "\n".join(lines)


def append_default_rule(md_content: str, field: str, value: str) -> str:
    """Add or update a document-level default in ## Document Defaults."""
    section_header = "## Document Defaults"
    if section_header not in md_content:
        md_content += f"\n{section_header}\n"

    field_norm = _normalise_field(field)
    display_field = _denormalise_field(field_norm)
    new_line = f"- {display_field}: {value}"

    lines = md_content.splitlines()
    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == section_header:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            break
        if in_section and stripped.startswith("- ") and ":" in stripped:
            existing_field_raw = stripped[2:].partition(":")[0].strip()
            if _normalise_field(existing_field_raw) == field_norm:
                lines[i] = new_line
                return "\n".join(lines)

    # Not found — append
    insert_at = len(lines)
    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == section_header:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            insert_at = i
            break
    lines.insert(insert_at, new_line)
    return "\n".join(lines)


def append_ai_instruction(md_content: str, instruction: str) -> str:
    """Append a line to ## AI Behaviour Instructions."""
    section_header = "## AI Behaviour Instructions"
    if section_header not in md_content:
        md_content += f"\n{section_header}\n"

    new_line = f"- {instruction}"
    lines = md_content.splitlines()
    insert_at = len(lines)
    in_section = False
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == section_header:
            in_section = True
            continue
        if in_section and stripped.startswith("## "):
            insert_at = i
            break
    lines.insert(insert_at, new_line)
    return "\n".join(lines)


def check_dedup(md_content: str, vendor: str | None, field: str, value: str) -> bool:
    """
    Return True if an identical (or near-duplicate) rule already exists in the MD.
    """
    parsed = parse_rules(md_content)
    field_norm = _normalise_field(field)

    # Check vendor rules
    if vendor:
        for vr in parsed["vendors"]:
            if _vendor_matches(vr["vendor"], vendor):
                existing_val = vr["fields"].get(field_norm, "")
                if str(existing_val).strip().lower() == str(value).strip().lower():
                    return True

    # Check defaults
    for d in parsed["defaults"]:
        if d["field"] == field_norm and str(d["value"]).strip().lower() == str(value).strip().lower():
            return True

    return False


# ── Display helpers ───────────────────────────────────────────────────────────

_FIELD_DISPLAY: dict[str, str] = {
    "account_code": "Account",
    "tax_code": "Tax Code",
    "currency": "Currency",
    "transaction_type": "Transaction Type",
    "category": "Category",
    "memo": "Memo",
    "payment_terms": "Payment Terms",
}


def _denormalise_field(field: str) -> str:
    return _FIELD_DISPLAY.get(field, field.replace("_", " ").title())

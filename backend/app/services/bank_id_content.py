"""Bank-ID visible-content helpers (Slice A).

Never promote hidden reasoning / thinking fields as bank identity.
Only ``message.content``-equivalent visible text may yield a bank_id.
"""
from __future__ import annotations

import json
import re
from typing import Any


_BANK_ID_JSON_RE = re.compile(
    r'\{\s*"bank_id"\s*:\s*"([A-Za-z0-9_\-]+)"\s*\}',
    re.IGNORECASE,
)


def visible_bank_id_from_content(content: str | None, *, known: set[str] | None = None) -> str | None:
    """Parse bank_id from visible model content only.

    Returns None when content is empty or unparseable — callers must not
    fall back to reasoning_content or similar hidden fields.
    """
    text = (content or "").strip()
    if not text:
        return None
    # Strip common fences
    if "```" in text:
        parts = text.split("```")
        for p in parts:
            p = p.strip()
            if p.lower().startswith("json"):
                p = p[4:].strip()
            if "bank_id" in p:
                text = p
                break
    bank_id: str | None = None
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("bank_id") is not None:
            bank_id = str(obj.get("bank_id")).strip().upper()
    except Exception:
        m = _BANK_ID_JSON_RE.search(text)
        if m:
            bank_id = m.group(1).strip().upper()
    if not bank_id or bank_id == "UNKNOWN":
        return None
    if known is not None and bank_id not in known:
        return None
    return bank_id


def bank_id_prompt_suffix() -> str:
    """Appended to bank-ID prompts — demand visible JSON content only."""
    return (
        "\n\nOUTPUT RULES:\n"
        "- Reply with ONLY visible JSON: {\"bank_id\": \"CODE\"}\n"
        "- Do not put the answer only in hidden thinking/reasoning.\n"
        "- bank_id must be one of the listed codes or UNKNOWN.\n"
    )

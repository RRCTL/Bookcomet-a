"""
AI-powered reconciliation service.

Combines duplicate detection + intelligent transaction matching via LLM.
The AI analyses bank vs ledger transactions in a single pass and returns:
  • duplicate alerts (within bank transactions)
  • match suggestions (bank ↔ ledger)
  • a plain-language summary

Server post-filters matches so only real IDs with equal absolute amounts are returned
(standard bank reconciliation: tick the same economic event; leave timing gaps unmatched).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.services.ai_chat_client import deploy_chat_client

logger = logging.getLogger(__name__)

_AMOUNT_TOLERANCE = 0.01

_SYSTEM_PROMPT = """\
You are a Hong Kong accounting reconciliation AI for bank reconciliation (BANK statement vs AR/AP books).

## Accounting policy (must follow)
1. Match only the same economic event: bank statement line ↔ ledger (AR/AP) line that already exists.
2. Absolute amounts MUST be equal. If amounts differ, leave unmatched (timing difference or need a proper adjusting entry later — never invent a bank/ledger row).
3. Never create, invent, or request virtual / remainder / partial / balancing transactions or amounts.
4. Prefer Reference / voucher evidence; then date proximity; then counterparty/memo similarity.
5. Unmatched is correct when the counterpart is not yet on the other side.

## Tasks (in order)
1. Detect duplicate records within Bank Transactions (same amount + date, or same account + amount + date).
2. Match Bank Transactions against Ledger Transactions using the policy above.

## Soft scoring (among equal-amount candidates only; max 4)
- Amount matches exactly (+1) — REQUIRED to propose a match
- Dates within 7 days (+1)
- Voucher/reference number matches (+1)
- Counterparty name or memo text is similar (+1)

## Output JSON only (no markdown fences)
{
  "duplicates": [
    {
      "txn_ids": ["<bank_id_A>", "<bank_id_B>"],
      "reason": "Brief reason for the duplicate",
      "level": 3
    }
  ],
  "matches": [
    {
      "bank_txn_id": "<bank_id>",
      "ledger_txn_id": "<ledger_id>",
      "score": 0.95,
      "match_type": "1:1",
      "ai_reason": "Brief reason for the match"
    }
  ],
  "summary": "Found X duplicate(s), matched Y pair(s), Z transaction(s) remaining unmatched."
}

## Rules
- duplicates.level: 1=low risk, 2=same-batch duplicate, 3=cross-batch duplicate, 4=exact account + amount + date match
- match_type: "1:1" | "1:N" | "N:1" | "N:M"
- All IDs must come exactly from the input data; never fabricate IDs
- Each bank or ledger transaction may appear in at most one matches entry
- If there are no duplicates or no matches, return the corresponding empty array
"""

MAX_TXNS_EACH = 80  # token safety guard — frontend batches stay well below this


def _fmt_bank(t: dict) -> str:
    return (
        f"  ID={t.get('id', '')} | "
        f"Date={t.get('bank_date') or t.get('date', '')} | "
        f"Amount={t.get('amount', '')} {t.get('currency', 'HKD')} | "
        f"Ref={t.get('reference') or t.get('doc_id', '')} | "
        f"Memo={t.get('description_raw') or t.get('memo', '')}"
    )


def _fmt_ledger(t: dict) -> str:
    return (
        f"  ID={t.get('id', '')} | "
        f"Date={t.get('book_date') or t.get('date', '')} | "
        f"Amount={t.get('amount', '')} {t.get('currency', 'HKD')} | "
        f"Ref={t.get('reference') or t.get('doc_id', '')} | "
        f"Counterparty={t.get('counterparty', '')}"
    )


def _parse_llm_json(content: str) -> dict:
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content).strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        logger.error("AI returned non-JSON response: %s", content[:400])
        return {
            "duplicates": [],
            "matches": [],
            "summary": "AI response format was invalid. Please try again.",
        }


def _amount_abs(raw: Any) -> float:
    try:
        return abs(float(raw or 0))
    except (TypeError, ValueError):
        return 0.0


def _amounts_equal(a: float, b: float) -> bool:
    return abs(a - b) <= _AMOUNT_TOLERANCE


def filter_valid_matches(
    matches: list[Any],
    bank_txns: list[dict],
    ledger_txns: list[dict],
) -> tuple[list[dict], int]:
    """Keep only 1:1 pairs with real IDs and equal absolute amounts. Drops invent/unequal pairs."""
    bank_by_id = {str(t.get("id", "")): t for t in bank_txns if t.get("id")}
    ledger_by_id = {str(t.get("id", "")): t for t in ledger_txns if t.get("id")}
    used_bank: set[str] = set()
    used_ledger: set[str] = set()
    kept: list[dict] = []
    dropped = 0

    for raw in matches or []:
        if not isinstance(raw, dict):
            dropped += 1
            continue
        bid = str(raw.get("bank_txn_id") or "").strip()
        lid = str(raw.get("ledger_txn_id") or "").strip()
        if not bid or not lid or bid not in bank_by_id or lid not in ledger_by_id:
            dropped += 1
            continue
        if bid in used_bank or lid in used_ledger:
            dropped += 1
            continue
        b_amt = _amount_abs(bank_by_id[bid].get("amount"))
        l_amt = _amount_abs(ledger_by_id[lid].get("amount"))
        if not _amounts_equal(b_amt, l_amt):
            dropped += 1
            continue
        used_bank.add(bid)
        used_ledger.add(lid)
        kept.append({
            "bank_txn_id": bid,
            "ledger_txn_id": lid,
            "score": raw.get("score"),
            "match_type": raw.get("match_type") or "1:1",
            "ai_reason": raw.get("ai_reason") or "",
        })

    return kept, dropped


def _call_llm(bank_txns: list[dict], ledger_txns: list[dict]) -> tuple[dict, dict]:
    """Send transactions to LLM and return (parsed_result, raw_response)."""
    bank_sample = bank_txns[:MAX_TXNS_EACH]
    ledger_sample = ledger_txns[:MAX_TXNS_EACH]

    truncation_note = ""
    if len(bank_txns) > MAX_TXNS_EACH or len(ledger_txns) > MAX_TXNS_EACH:
        truncation_note = (
            f"\nNote: only the first {MAX_TXNS_EACH} bank and "
            f"{MAX_TXNS_EACH} ledger rows were analyzed."
        )

    bank_lines = "\n".join(_fmt_bank(t) for t in bank_sample)
    ledger_lines = "\n".join(_fmt_ledger(t) for t in ledger_sample)

    user_msg = (
        f"**Bank Transactions ({len(bank_sample)} rows):**\n{bank_lines}\n\n"
        f"**Ledger Transactions ({len(ledger_sample)} rows):**\n{ledger_lines}\n"
        f"{truncation_note}\n\n"
        "Propose matches only when absolute Amounts are equal. "
        "Leave unequal amounts unmatched. Reply in JSON only, in English."
    )

    content, data = deploy_chat_client.complete(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
    )
    return _parse_llm_json(content), data


def run_ai_match(
    bank_txns: list[dict],
    ledger_txns: list[dict],
) -> tuple[dict, dict]:
    """
    Run AI-powered duplicate detection + transaction matching.

    Returns (result_dict, raw_llm_response).

    result_dict schema:
    {
        "duplicates": [{"txn_ids": [...], "reason": "...", "level": 1-4}],
        "matches":    [{"bank_txn_id": "...", "ledger_txn_id": "...",
                        "score": float, "match_type": "1:1", "ai_reason": "..."}],
        "summary":    "..."
    }
    """
    if not bank_txns or not ledger_txns:
        return {
            "duplicates": [],
            "matches": [],
            "summary": "Add bank and ledger rows before running AI match.",
        }, {}

    if not deploy_chat_client.api_key:
        raise ValueError(
            "LLM API not configured. Set LLM_API_KEY in backend/.env and restart the server."
        )

    result, raw = _call_llm(bank_txns, ledger_txns)
    kept, dropped = filter_valid_matches(result.get("matches") or [], bank_txns, ledger_txns)
    result["matches"] = kept
    if dropped:
        base = (result.get("summary") or "").strip()
        note = f" Rejected {dropped} unequal or invalid proposed pair(s)."
        result["summary"] = (base + note).strip() if base else note.strip()
        logger.info("[ai-match] dropped %s invalid/unequal LLM match(es); kept %s", dropped, len(kept))
    return result, raw

"""HSBC zero-fabrication admission (Slice 0).

Ordinary bank transactions require physical amount evidence. Free-form VLM
``transactions[]`` without layout/prescan amount evidence must not become
canonical review/export/reconciliation/posting rows.

No page index, row ordinal, amount, payee, filename, model ID, or gateway URL
may appear here as parser rules or fixtures.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, MutableMapping, Sequence

PAGE_STATUS_NEEDS_LAYOUT_REVIEW = "needs_layout_review"
FLAG_NEEDS_LAYOUT_REVIEW = "needs_layout_review"
FLAG_VLM_FINANCIAL_ABSTAINED = "vlm_financial_abstained"
FLAG_UNRESOLVED_ANCHOR = "unresolved_anchor"
FLAG_MISSING_AMOUNT_EVIDENCE = "missing_amount_evidence"


@dataclass
class PageAdmissionResult:
    """Outcome of admitting (or abstaining) candidates for one page."""

    canonical_rows: list[dict[str, Any]] = field(default_factory=list)
    unresolved_anchors: list[dict[str, Any]] = field(default_factory=list)
    page_status: str | None = None
    reason_codes: list[str] = field(default_factory=list)
    abstained: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_row_count": len(self.canonical_rows),
            "unresolved_anchor_count": len(self.unresolved_anchors),
            "page_status": self.page_status,
            "reason_codes": list(self.reason_codes),
            "abstained": self.abstained,
        }


def has_deterministic_amount_evidence(*, amount_anchor_count: int) -> bool:
    """True when a deterministic amount pre-scan found at least one Cr/Dr anchor."""
    return int(amount_anchor_count or 0) > 0


def row_has_amount_evidence(row: Mapping[str, Any]) -> bool:
    """Structural evidence only — never trust free-form VLM amount fields alone."""
    if row.get("row_anchor_id") or row.get("_hsbc_row_id"):
        tokens = row.get("numeric_token_ids")
        if isinstance(tokens, (list, tuple)) and len(tokens) > 0:
            return True
        prov = row.get("column_provenance")
        if isinstance(prov, dict):
            roles = {str(v).strip().lower() for v in prov.values() if v}
            if roles & {"prescan_cr", "prescan_dr", "prescan_balance_band"}:
                return True
    if row.get("has_balance_band_token") is True and (
        row.get("row_anchor_id") or row.get("_hsbc_row_id")
    ):
        # Balance-only B/F / snapshot still needs an anchor identity.
        kind = str(row.get("row_kind") or "").strip().lower()
        if kind in {"brought_forward", "balance_snapshot"}:
            return True
    return False


def may_emit_transaction(candidate: Mapping[str, Any]) -> bool:
    """Ordinary txn admission: one evidence-backed amount side, not summary-only.

    Slice 0 enforces evidence presence. Full geometry bbox checks arrive in Slice 1.
    """
    kind = str(candidate.get("row_kind") or "transaction").strip().lower()
    if kind in {"brought_forward", "balance_snapshot"}:
        return row_has_amount_evidence(candidate) and candidate.get("balance") is not None

    desc = str(candidate.get("備註") or candidate.get("description") or "").strip()
    if desc == "無交易":
        return True

    if not row_has_amount_evidence(candidate):
        return False

    dep = candidate.get("deposit")
    wdw = candidate.get("withdrawal")
    has_dep = dep is not None and str(dep).strip() not in {"", "0", "0.0", "0.00"}
    has_wdw = wdw is not None and str(wdw).strip() not in {"", "0", "0.0", "0.00"}
    if has_dep and has_wdw:
        return False
    if not has_dep and not has_wdw:
        return False
    return True


def hsbc_ar_manager_allowed(page_txns: Sequence[Mapping[str, Any]]) -> bool:
    """AR manager may run only when every primary row is already evidence-backed."""
    if not page_txns:
        return False
    return all(row_has_amount_evidence(r) for r in page_txns if isinstance(r, Mapping))


def admit_page_candidates(
    *,
    candidates: Sequence[Mapping[str, Any]],
    amount_anchor_count: int,
    source: str = "hsbc",
) -> PageAdmissionResult:
    """Admit evidence-backed rows; abstain when no deterministic amount evidence.

    When ``amount_anchor_count == 0``, free-form VLM candidates are discarded and
    the page is marked ``needs_layout_review``. Physical unresolved anchors are
    only emitted when an anchor id exists without inventing amounts.
    """
    result = PageAdmissionResult()
    if not has_deterministic_amount_evidence(amount_anchor_count=amount_anchor_count):
        result.abstained = True
        result.page_status = PAGE_STATUS_NEEDS_LAYOUT_REVIEW
        result.reason_codes = [
            FLAG_NEEDS_LAYOUT_REVIEW,
            FLAG_VLM_FINANCIAL_ABSTAINED,
            FLAG_MISSING_AMOUNT_EVIDENCE,
        ]
        # Do not invent unresolved anchors from VLM-only objects.
        for cand in candidates:
            if not isinstance(cand, Mapping):
                continue
            anchor = cand.get("row_anchor_id") or cand.get("_hsbc_row_id")
            if not anchor:
                continue
            unresolved = dict(cand)
            unresolved["exportable"] = False
            unresolved["needs_review"] = True
            unresolved["row_kind"] = "unresolved_anchor"
            flags = list(unresolved.get("validation_flags") or [])
            for f in (FLAG_UNRESOLVED_ANCHOR, FLAG_MISSING_AMOUNT_EVIDENCE):
                if f not in flags:
                    flags.append(f)
            unresolved["validation_flags"] = flags
            # Strip invented monetary fields — keep region identity only.
            unresolved["deposit"] = None
            unresolved["withdrawal"] = None
            unresolved["balance"] = unresolved.get("balance")  # only if already evidence-backed
            if not row_has_amount_evidence(unresolved):
                unresolved["balance"] = None
            result.unresolved_anchors.append(unresolved)
        return result

    for cand in candidates:
        if not isinstance(cand, Mapping):
            continue
        row = dict(cand)
        if may_emit_transaction(row):
            row.setdefault("exportable", True)
            result.canonical_rows.append(row)
            continue
        anchor = row.get("row_anchor_id") or row.get("_hsbc_row_id")
        if anchor:
            row["exportable"] = False
            row["needs_review"] = True
            row["row_kind"] = str(row.get("row_kind") or FLAG_UNRESOLVED_ANCHOR)
            flags = list(row.get("validation_flags") or [])
            if FLAG_UNRESOLVED_ANCHOR not in flags:
                flags.append(FLAG_UNRESOLVED_ANCHOR)
            if FLAG_MISSING_AMOUNT_EVIDENCE not in flags:
                flags.append(FLAG_MISSING_AMOUNT_EVIDENCE)
            row["validation_flags"] = flags
            result.unresolved_anchors.append(row)
        # else: drop — no physical region to hang a review candidate on

    if not result.canonical_rows and amount_anchor_count > 0:
        # Activity evidence existed but nothing admitted — still a layout/coverage review.
        result.page_status = PAGE_STATUS_NEEDS_LAYOUT_REVIEW
        result.reason_codes = [FLAG_NEEDS_LAYOUT_REVIEW, "admission_emitted_zero"]
    return result


def mark_page_needs_layout_review(
    page_verification_out: MutableMapping[int, str] | None,
    page_num_1based: int,
) -> None:
    if page_verification_out is None:
        return
    page_verification_out[int(page_num_1based)] = PAGE_STATUS_NEEDS_LAYOUT_REVIEW


def export_blocked_by_admission(rows: Sequence[Mapping[str, Any]]) -> bool:
    """Block export when unresolved / abstention flags are present."""
    blocking = {
        FLAG_NEEDS_LAYOUT_REVIEW,
        FLAG_VLM_FINANCIAL_ABSTAINED,
        FLAG_UNRESOLVED_ANCHOR,
        FLAG_MISSING_AMOUNT_EVIDENCE,
    }
    for r in rows:
        if r.get("exportable") is False:
            return True
        if str(r.get("row_kind") or "") == FLAG_UNRESOLVED_ANCHOR:
            return True
        flags = {str(x) for x in (r.get("validation_flags") or [])}
        if flags & blocking:
            return True
    return False

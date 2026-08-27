"""HSBC parser contracts A–D (deterministic validation; no page/row hardcodes).

Synthetic fixtures exercise these contracts. Private incident ordinals/pages
must never appear here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

ContractId = Literal["A_coverage", "B_column_band", "C_section", "D_provenance"]


@dataclass
class ContractIssue:
    contract: ContractId
    reason: str
    row_anchor_id: str | None = None
    section_id: str | None = None
    severity: Literal["blocking", "review"] = "blocking"

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "reason": self.reason,
            "row_anchor_id": self.row_anchor_id,
            "section_id": self.section_id,
            "severity": self.severity,
        }


@dataclass
class ContractResult:
    ok: bool
    issues: list[ContractIssue] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [i.to_dict() for i in self.issues],
            "flags": list(self.flags),
        }


def validate_contract_a_coverage(
    *,
    has_txn_header: bool,
    amount_anchor_count: int,
    emitted_row_count: int,
    section_id: str | None = None,
) -> ContractResult:
    """Activity page/section with anchors must emit rows or fail coverage."""
    issues: list[ContractIssue] = []
    flags: list[str] = []
    is_activity = bool(has_txn_header) and int(amount_anchor_count) > 0
    if is_activity and int(emitted_row_count) == 0:
        issues.append(
            ContractIssue(
                contract="A_coverage",
                reason="activity_page_without_emitted_rows",
                section_id=section_id,
                severity="blocking",
            )
        )
        flags.append("coverage_failed")
    return ContractResult(ok=not issues, issues=issues, flags=flags)


def _token_column(token: dict[str, Any]) -> str | None:
    col = token.get("column") or token.get("col") or token.get("assigned_column")
    if col is None:
        return None
    c = str(col).strip().lower()
    if c in {"cr", "deposit", "dep"}:
        return "deposit"
    if c in {"dr", "withdrawal", "wdw", "wit"}:
        return "withdrawal"
    if c in {"bal", "balance"}:
        return "balance"
    return c or None


def validate_contract_b_column_band(
    *,
    row: dict[str, Any],
    tokens: Sequence[dict[str, Any]] | None = None,
) -> ContractResult:
    """Balance-band tokens must not become deposit/withdrawal; no dual amounts."""
    issues: list[ContractIssue] = []
    flags: list[str] = []
    dep = row.get("deposit")
    wdw = row.get("withdrawal")
    if dep is not None and wdw is not None:
        issues.append(
            ContractIssue(
                contract="B_column_band",
                reason="dual_deposit_and_withdrawal",
                row_anchor_id=row.get("row_anchor_id") or row.get("_hsbc_row_id"),
                section_id=row.get("section_id") or row.get("_hsbc_section_id"),
                severity="blocking",
            )
        )
        flags.append("amount_conflict")

    for tok in tokens or row.get("numeric_tokens") or []:
        if not isinstance(tok, dict):
            continue
        band = str(tok.get("band") or tok.get("x_band") or "").lower()
        assigned = _token_column(tok)
        if band in {"balance", "bal"} and assigned in {"deposit", "withdrawal"}:
            issues.append(
                ContractIssue(
                    contract="B_column_band",
                    reason="balance_band_written_as_amount",
                    row_anchor_id=row.get("row_anchor_id") or row.get("_hsbc_row_id"),
                    section_id=row.get("section_id") or row.get("_hsbc_section_id"),
                    severity="blocking",
                )
            )
            flags.append("column_band_violation")
        if tok.get("ambiguous") or tok.get("band_confidence", 1.0) < 0.5:
            flags.append("needs_review")
            if "ambiguous_column_band" not in flags:
                flags.append("ambiguous_column_band")
            issues.append(
                ContractIssue(
                    contract="B_column_band",
                    reason="ambiguous_column_band",
                    row_anchor_id=row.get("row_anchor_id") or row.get("_hsbc_row_id"),
                    section_id=row.get("section_id") or row.get("_hsbc_section_id"),
                    severity="review",
                )
            )
    # Provenance: if amounts present, require column_provenance when provided schema expects it
    prov = row.get("column_provenance")
    if (dep is not None or wdw is not None) and prov is not None:
        if not isinstance(prov, dict) or not prov:
            issues.append(
                ContractIssue(
                    contract="B_column_band",
                    reason="missing_column_provenance",
                    row_anchor_id=row.get("row_anchor_id") or row.get("_hsbc_row_id"),
                    severity="review",
                )
            )
            flags.append("needs_review")
    return ContractResult(ok=not any(i.severity == "blocking" for i in issues), issues=issues, flags=flags)


def validate_contract_c_section(
    *,
    section_id: str,
    detected_anchor_ids: Sequence[str],
    emitted_anchor_ids: Sequence[str],
    has_balance_only_account: bool = False,
    has_account_balance_snapshot: bool = False,
) -> ContractResult:
    """Section isolation: first valid anchor must emit; balance-only needs snapshot."""
    issues: list[ContractIssue] = []
    flags: list[str] = []
    detected = [str(x) for x in detected_anchor_ids]
    emitted = set(str(x) for x in emitted_anchor_ids)
    if detected:
        first = detected[0]
        if first not in emitted:
            issues.append(
                ContractIssue(
                    contract="C_section",
                    reason="first_valid_anchor_not_emitted",
                    row_anchor_id=first,
                    section_id=section_id,
                    severity="blocking",
                )
            )
            flags.append("section_first_anchor_missed")
        extras = emitted - set(detected)
        if extras:
            issues.append(
                ContractIssue(
                    contract="C_section",
                    reason="emitted_anchor_outside_section",
                    section_id=section_id,
                    severity="blocking",
                )
            )
            flags.append("section_boundary_violation")
    if has_balance_only_account and not has_account_balance_snapshot:
        issues.append(
            ContractIssue(
                contract="C_section",
                reason="missing_account_balance_snapshot",
                section_id=section_id,
                severity="blocking",
            )
        )
        flags.append("missing_balance_snapshot")
    return ContractResult(ok=not issues, issues=issues, flags=flags)


_REQUIRED_PROVENANCE = (
    "source_page",
    "section_id",
    "row_anchor_id",
    "column_provenance",
)


def validate_contract_d_provenance(row: dict[str, Any]) -> ContractResult:
    """Every exportable row must carry provenance; export must not invent roles."""
    issues: list[ContractIssue] = []
    flags: list[str] = []
    # Accept aliases used by HSBC V2 merge
    normalized = {
        "source_page": row.get("source_page") or row.get("_source_page"),
        "section_id": row.get("section_id") or row.get("_hsbc_section_id"),
        "row_anchor_id": row.get("row_anchor_id") or row.get("_hsbc_row_id"),
        "column_provenance": row.get("column_provenance"),
        "numeric_token_ids": row.get("numeric_token_ids"),
        "validation_flags": row.get("validation_flags"),
    }
    for key in _REQUIRED_PROVENANCE:
        if normalized.get(key) in (None, "", {}, []):
            issues.append(
                ContractIssue(
                    contract="D_provenance",
                    reason=f"missing_{key}",
                    row_anchor_id=normalized.get("row_anchor_id"),
                    section_id=normalized.get("section_id"),
                    severity="review",
                )
            )
            flags.append("needs_review")
            flags.append(f"missing_{key}")
    # Export integrity: do not allow a row marked role_swapped
    if row.get("_role_rewritten") or row.get("amount_side_rewritten"):
        issues.append(
            ContractIssue(
                contract="D_provenance",
                reason="numeric_role_rewritten",
                row_anchor_id=normalized.get("row_anchor_id"),
                severity="blocking",
            )
        )
        flags.append("export_role_violation")
    blocking = [i for i in issues if i.severity == "blocking"]
    return ContractResult(ok=not blocking, issues=issues, flags=flags)


def apply_contracts_to_row(
    row: dict[str, Any],
    *,
    tokens: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Mutate a shallow copy with validation_flags / needs_review from B+D."""
    out = dict(row)
    flags: list[str] = list(out.get("validation_flags") or [])
    b = validate_contract_b_column_band(row=out, tokens=tokens)
    d = validate_contract_d_provenance(out)
    for f in b.flags + d.flags:
        if f not in flags:
            flags.append(f)
    out["validation_flags"] = flags
    # Informational flags (e.g. expected blank HSBC balance) must not force review.
    from app.services.extraction_validation import BANK_INFO_ONLY_VALIDATION_FLAGS

    review_driving = [
        f for f in flags if f not in BANK_INFO_ONLY_VALIDATION_FLAGS
    ]
    if review_driving or b.issues or d.issues:
        out["needs_review"] = True
    out["_contract_issues"] = [i.to_dict() for i in b.issues + d.issues]
    out["_contracts_ok"] = b.ok and d.ok
    return out


def export_blocked_by_contracts(rows: Sequence[dict[str, Any]]) -> bool:
    """True when any row has a blocking contract failure (silent bypass forbidden)."""
    from app.services.hsbc_admission import export_blocked_by_admission

    if export_blocked_by_admission(rows):
        return True
    for r in rows:
        if r.get("_contracts_ok") is False:
            return True
        flags = set(str(x) for x in (r.get("validation_flags") or []))
        if flags & {
            "coverage_failed",
            "amount_conflict",
            "column_band_violation",
            "section_boundary_violation",
            "export_role_violation",
            "needs_layout_review",
            "vlm_financial_abstained",
        }:
            return True
        for issue in r.get("_contract_issues") or []:
            if isinstance(issue, dict) and issue.get("severity") == "blocking":
                return True
    return False

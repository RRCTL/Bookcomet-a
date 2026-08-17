"""
Shared merge core for AP second VLM (cross-model) verification.

Primary extraction populates `ai_enhanced`; cross VLM re-runs structured extraction
with `AP_CROSS_VLM_MODEL`. This module applies an in-place merge policy onto the
primary dict shape (same `tsv_rows` contract as `buildSpreadsheetFromOcrResult`).
"""
from __future__ import annotations

import copy
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _nonempty_scalar(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, (dict, list)):
        return bool(v)
    return str(v).strip() != ""


def _min_tsv_confidence(cross: dict[str, Any]) -> float | None:
    rows = cross.get("tsv_rows")
    if not isinstance(rows, list) or not rows:
        return None
    vals: list[float] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        raw = r.get("confidence", "")
        s = str(raw).strip()
        if not s:
            continue
        s = s.rstrip("%")
        try:
            f = float(s)
            if f > 1.0:
                f = f / 100.0
            vals.append(max(0.0, min(1.0, f)))
        except ValueError:
            continue
    return min(vals) if vals else None


def _merge_row_aggressive(primary: dict[str, Any], cross: dict[str, Any]) -> dict[str, Any]:
    out = dict(primary)
    if not isinstance(cross, dict):
        return out
    for k, v in cross.items():
        if _nonempty_scalar(v):
            out[k] = copy.deepcopy(v) if isinstance(v, (dict, list)) else v
    return out


def _merge_receipt_lists(
    primary_list: list[Any],
    cross_list: list[Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, p in enumerate(primary_list):
        if not isinstance(p, dict):
            continue
        if i < len(cross_list) and isinstance(cross_list[i], dict):
            out.append(_merge_row_aggressive(p, cross_list[i]))
        else:
            out.append(dict(p))
    for j in range(len(primary_list), len(cross_list)):
        c = cross_list[j]
        if isinstance(c, dict):
            out.append(copy.deepcopy(c))
    return out


def merge_ap_ai_enhanced_primary_with_cross(
    primary: dict[str, Any],
    cross: dict[str, Any],
    *,
    cross_model: str,
    policy: str = "aggressive_overwrite",
) -> dict[str, Any]:
    """
    Return a deep copy of `primary` with non-empty fields from `cross` merged in.
    Preserves row count: primary rows are the anchor; cross values overwrite when set.
    Extra cross-only rows are appended. Preserves `receipts` when present.
    """
    out = copy.deepcopy(primary)
    if not isinstance(cross, dict):
        return out

    audit: dict[str, Any] = {
        "cross_model": cross_model,
        "policy": policy,
        "merged_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    ptsv = out.get("tsv_rows")
    xtsv = cross.get("tsv_rows")
    if isinstance(ptsv, list) and isinstance(xtsv, list) and xtsv:
        merged_rows: list[dict[str, Any]] = []
        for i, p_row in enumerate(ptsv):
            if not isinstance(p_row, dict):
                continue
            if i < len(xtsv) and isinstance(xtsv[i], dict):
                merged_rows.append(_merge_row_aggressive(p_row, xtsv[i]))
            else:
                merged_rows.append(dict(p_row))
        for j in range(len(ptsv), len(xtsv)):
            xr = xtsv[j]
            if isinstance(xr, dict):
                merged_rows.append(copy.deepcopy(xr))
        out["tsv_rows"] = merged_rows
        audit["tsv_rows_merged"] = len(merged_rows)
    elif isinstance(xtsv, list) and xtsv and not (isinstance(ptsv, list) and ptsv):
        out["tsv_rows"] = copy.deepcopy(xtsv)
        audit["tsv_rows_merged"] = len(xtsv)
        audit["note"] = "primary_had_no_tsv_rows"
    else:
        audit["skip_tsv"] = "cross_no_tsv_rows"

    preceipts = out.get("receipts")
    xreceipts = cross.get("receipts")
    if isinstance(preceipts, list) and isinstance(xreceipts, list) and xreceipts:
        out["receipts"] = _merge_receipt_lists(preceipts, xreceipts)
        audit["receipts_merged"] = len(out["receipts"])

    if _nonempty_scalar(cross.get("fx_reference")):
        out["fx_reference"] = copy.deepcopy(cross["fx_reference"])

    es = str(out.get("extraction_source") or "")
    out["extraction_source"] = (
        f"{es}|cross_vlm_merged" if es else "cross_vlm_merged"
    ).lstrip("|")

    out["ap_cross_vlm_audit"] = audit
    return out


def cross_extraction_passes_confidence_gate(
    cross: dict[str, Any],
    min_confidence: float,
) -> bool:
    """When min_confidence <= 0, always True. Otherwise require min cross row confidence."""
    if min_confidence <= 0.0:
        return True
    m = _min_tsv_confidence(cross)
    if m is None:
        logger.info("[AP cross-VLM] confidence gate: no parseable cross confidence; skip merge")
        return False
    ok = m >= min_confidence
    if not ok:
        logger.info(
            "[AP cross-VLM] confidence gate: min_cross=%s < threshold=%s; skip merge",
            m,
            min_confidence,
        )
    return ok

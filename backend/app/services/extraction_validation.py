"""
Deterministic extraction checks and unified row metadata (needs_review, validation_flags)
for OCR / VLM pipelines. Intended for merging into spreadsheet rows and AI chat context.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping, MutableMapping, Sequence

import os

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class ValidationResult:
    needs_review: bool
    validation_flags: tuple[str, ...]
    """Optional (key, value) pairs merged into row for review-only suggestions (never overwrites filled fields)."""
    row_hints: tuple[tuple[str, str], ...] = ()

    def to_dict_fields(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "needs_review": self.needs_review,
            "validation_flags": list(self.validation_flags),
        }
        if self.row_hints:
            d["review_hints"] = {k: v for k, v in self.row_hints}
        return d


def merge_validation_into_row(
    row: MutableMapping[str, Any],
    result: ValidationResult,
    *,
    preserve_existing_flags: bool = True,
) -> MutableMapping[str, Any]:
    """Attach needs_review / validation_flags. Optionally merge flags with existing."""
    merged_flags: list[str] = []
    if preserve_existing_flags:
        prev = row.get("validation_flags")
        if isinstance(prev, list):
            merged_flags.extend(str(x) for x in prev if x)
    merged_flags.extend(result.validation_flags)
    # Dedupe preserve order
    seen: set[str] = set()
    uniq = []
    for f in merged_flags:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    row["validation_flags"] = uniq
    row["needs_review"] = bool(row.get("needs_review")) or result.needs_review
    for key, val in result.row_hints:
        prev = row.get(key)
        if prev is None or (isinstance(prev, str) and not prev.strip()):
            row[key] = val
    return row


def _money_decimal(v: Any) -> Decimal | None:
    if v is None:
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return Decimal(s).quantize(Decimal("0.01"), ROUND_HALF_UP)
    except Exception:
        return None


def _parse_float_loose(v: Any) -> float | None:
    d = _money_decimal(v)
    if d is None:
        return None
    return float(d)


def format_ocr_layout_hint_from_lines(
    lines: Sequence[Any] | None,
    *,
    max_lines: int = 24,
    max_chars: int = 900,
) -> str:
    """
    Build a compact hint from OcrLine-like objects (text + bbox list [x1,y1,x2,y2]).
    Returns empty string when no lines.
    """
    if not lines:
        return ""
    parts: list[str] = []
    total = 0
    for ln in lines[:max_lines]:
        text = str(getattr(ln, "text", "") or "").strip().replace("\n", " ")
        if not text:
            continue
        bbox = getattr(ln, "bbox", None)
        if isinstance(bbox, (list, tuple)) and len(bbox) >= 4:
            bx = ",".join(str(int(x)) for x in bbox[:4])
            seg = f"[{bx}] {text[:120]}"
        else:
            seg = text[:120]
        if total + len(seg) + 1 > max_chars:
            break
        parts.append(seg)
        total += len(seg) + 1
    return "\n".join(parts)


def validate_ar_ap_receipt(
    json_obj: Mapping[str, Any] | None,
    norm_row: Mapping[str, Any],
) -> ValidationResult:
    """Receipt / invoice row (AR or AP). json_obj may be None for TSV-only rows."""
    flags: list[str] = []

    amount = _parse_float_loose(norm_row.get("amount"))
    payee = str(norm_row.get("payee") or "").strip()
    date_s = str(norm_row.get("date") or "").strip()

    if not payee:
        flags.append("receipt_missing_merchant")
    if amount is None or amount <= 0:
        flags.append("receipt_missing_or_invalid_amount")
    if date_s and not _ISO_DATE_PATTERN.match(date_s):
        flags.append("receipt_date_format_suspicious")

    total_d: Decimal | None = None
    tax_d: Decimal | None = None
    sub_d: Decimal | None = None
    if json_obj:
        total_d = _money_decimal(json_obj.get("total_amount"))
        tax_d = _money_decimal(json_obj.get("tax_amount"))
        sub_d = _money_decimal(json_obj.get("subtotal_amount"))

        row_hints: list[tuple[str, str]] = []
        if tax_d is not None and sub_d is not None and total_d is not None:
            expected = (sub_d + tax_d).quantize(Decimal("0.01"), ROUND_HALF_UP)
            if abs(expected - total_d) > Decimal("0.02"):
                flags.append("tax_subtotal_total_mismatch")
        elif tax_d is not None and total_d is not None and sub_d is None:
            base = (total_d - tax_d).quantize(Decimal("0.01"), ROUND_HALF_UP)
            if base <= Decimal("0"):
                flags.append("tax_total_incoherent")
        elif tax_d is None and sub_d is not None and total_d is not None:
            sugg = (total_d - sub_d).quantize(Decimal("0.01"), ROUND_HALF_UP)
            if sugg > Decimal("0"):
                flags.append("tax_amount_suggested_for_review")
                row_hints.append(("review_suggested_tax_amount", str(sugg)))
        elif tax_d is not None and sub_d is None and total_d is not None:
            sugg = (total_d - tax_d).quantize(Decimal("0.01"), ROUND_HALF_UP)
            if sugg > Decimal("0"):
                flags.append("subtotal_suggested_for_review")
                row_hints.append(("review_suggested_subtotal_amount", str(sugg)))

        needs = bool(flags)
        return ValidationResult(
            needs_review=needs,
            validation_flags=tuple(flags),
            row_hints=tuple(row_hints),
        )

    needs = bool(flags)
    return ValidationResult(needs_review=needs, validation_flags=tuple(flags))


def validate_cheque_row(
    _json_obj: Mapping[str, Any] | None,
    norm_row: Mapping[str, Any],
) -> ValidationResult:
    flags: list[str] = []
    vch = str(norm_row.get("voucher_no") or "").strip()
    amt = _parse_float_loose(norm_row.get("amount"))
    date_s = str(norm_row.get("date") or "").strip()
    payer = str(norm_row.get("payer") or "").strip()
    payee = str(norm_row.get("payee") or "").strip()

    if amt is not None and amt > 0 and len(re.sub(r"\D", "", vch)) < 4:
        flags.append("cheque_number_missing_or_short")
    if date_s and not _ISO_DATE_PATTERN.match(date_s):
        flags.append("cheque_date_format_suspicious")
    if payer and payee and payer.lower() == payee.lower():
        flags.append("cheque_payer_payee_identical")

    return ValidationResult(needs_review=bool(flags), validation_flags=tuple(flags))


def validate_deposit_advice_row(
    _json_obj: Mapping[str, Any] | None,
    norm_row: Mapping[str, Any],
) -> ValidationResult:
    flags: list[str] = []
    amt = _parse_float_loose(norm_row.get("amount"))
    if amt is None or amt <= 0:
        flags.append("deposit_advice_missing_amount")
    date_s = str(norm_row.get("date") or "").strip()
    if not date_s:
        flags.append("deposit_advice_missing_date")
    return ValidationResult(needs_review=bool(flags), validation_flags=tuple(flags))


def validate_bank_transaction(txn: Mapping[str, Any]) -> ValidationResult:
    flags: list[str] = []

    desc0 = str(txn.get("備註") or txn.get("description") or "").strip()
    if desc0 == "無交易":
        return ValidationResult(needs_review=False, validation_flags=())

    dep = txn.get("deposit")
    wd = txn.get("withdrawal")
    dep_n = _parse_float_loose(dep)
    wd_n = _parse_float_loose(wd)

    has_dep = dep_n is not None and dep_n != 0
    has_wd = wd_n is not None and wd_n != 0
    if has_dep and has_wd:
        flags.append("bank_deposit_and_withdrawal_both_set")

    # Chinese keys from normalizer
    if not has_dep and not has_wd:
        c_dep = _parse_float_loose(txn.get("存入"))
        c_wd = _parse_float_loose(txn.get("提取"))
        has_cd = c_dep is not None and c_dep != 0
        has_cw = c_wd is not None and c_wd != 0
        if has_cd and has_cw:
            flags.append("bank_deposit_and_withdrawal_both_set")

    bal_raw = txn.get("balance")
    if bal_raw is None:
        bal_raw = txn.get("原幣結餘")
    bal_n = _parse_float_loose(bal_raw)
    if bal_n is None:
        flags.append("bank_balance_missing")

    tx_date = str(
        txn.get("transaction_date")
        or txn.get("bank_date")
        or txn.get("date")
        or "",
    ).strip()
    if not tx_date:
        flags.append("bank_transaction_date_missing")

    return ValidationResult(needs_review=bool(flags), validation_flags=tuple(flags))


def finalize_bank_transactions(
    rows: list[MutableMapping[str, Any]],
) -> list[MutableMapping[str, Any]]:
    """Attach validation flags and cross-row duplicate hints to bank VLM rows."""
    out: list[MutableMapping[str, Any]] = []
    for t in rows:
        if not isinstance(t, dict):
            continue
        txn: MutableMapping[str, Any] = dict(t)
        vr = validate_bank_transaction(txn)
        merge_validation_into_row(txn, vr)
        out.append(txn)
    apply_batch_duplicate_flags_bank(out)
    return out


def validate_other_record(rec: Mapping[str, Any]) -> ValidationResult:
    """record = { id, record_type, ...payload } as loaded for chat."""
    flags: list[str] = []
    rid = str(rec.get("id") or "").strip()
    rtype = str(rec.get("record_type") or "").strip().lower()
    if not rid:
        flags.append("other_missing_id")
    if rtype not in ("loan", "fixed_asset", ""):
        flags.append("other_unknown_record_type")
    if rtype == "loan":
        if not str(rec.get("lender_name") or "").strip():
            flags.append("other_loan_party_missing")
        if _parse_float_loose(rec.get("principal_amount")) is None:
            flags.append("other_principal_missing")
    elif rtype == "fixed_asset":
        if not str(rec.get("asset_name") or "").strip():
            flags.append("other_asset_name_missing")
    return ValidationResult(needs_review=bool(flags), validation_flags=tuple(flags))


def _ar_ap_file_key(row: Mapping[str, Any]) -> str:
    for key in ("file_position", "source_file", "filename", "source_page"):
        value = str(row.get(key) or "").strip()
        if value:
            return value.lower()
    return ""


def _normalized_amount(row: Mapping[str, Any]) -> str:
    raw = row.get("amount")
    if raw is None:
        raw = row.get("total_amount")
    if raw is None:
        raw = row.get("total")
    if raw is None:
        return ""
    text = str(raw).strip().replace(",", "")
    if not text:
        return ""
    try:
        return f"{float(text):.2f}"
    except ValueError:
        return text


def _normalized_currency(row: Mapping[str, Any]) -> str:
    return str(row.get("currency") or "HKD").strip().upper()


def _normalized_vendor(row: Mapping[str, Any]) -> str:
    return str(row.get("payee") or row.get("vendor") or "").strip().lower()


def _normalized_date(row: Mapping[str, Any]) -> str:
    return str(row.get("date") or row.get("invoice_date") or "").strip()


_OCR_NOISE_PATTERNS = (
    re.compile(r"analysis\s+summary", re.I),
    re.compile(r"^page\s+\d+\s+of\s+\d+$", re.I),
    re.compile(r"^continued\.?$", re.I),
)


def _row_has_amount(row: Mapping[str, Any]) -> bool:
    return bool(_normalized_amount(row))


def _is_ocr_noise_row(row: Mapping[str, Any]) -> bool:
    if _row_has_amount(row):
        return False
    vendor = _normalized_vendor(row)
    date = _normalized_date(row)
    if not vendor and not date:
        return True
    text_parts = [
        vendor,
        date,
        str(row.get("memo") or row.get("particulars") or row.get("description") or "").strip(),
        str(row.get("payee") or row.get("vendor") or "").strip(),
    ]
    combined = " ".join(p for p in text_parts if p).strip()
    if not combined:
        return True
    for pattern in _OCR_NOISE_PATTERNS:
        if pattern.search(combined):
            return True
    return False


def _ar_ap_row_keep_score(row: Mapping[str, Any]) -> float:
    score = 0.0
    if str(row.get("date") or row.get("invoice_date") or "").strip():
        score += 10.0
    if _normalized_amount(row):
        score += 10.0
    if str(row.get("payee") or row.get("vendor") or "").strip():
        score += 5.0
    conf_raw = str(row.get("confidence") or "").strip().replace("%", "")
    try:
        score += min(float(conf_raw) / 10.0, 10.0)
    except ValueError:
        pass
    if str(row.get("voucher_no") or row.get("invoice_number") or "").strip():
        score += 2.0
    return score


def _ar_ap_dedupe_bucket_key(row: Mapping[str, Any]) -> tuple[str, ...] | None:
    amount = _normalized_amount(row)
    if not amount:
        return None
    return (
        _ar_ap_file_key(row),
        amount,
        _normalized_vendor(row),
        _normalized_date(row),
    )


def _dedupe_indices_by_bucket(
    normalized: list[dict[str, Any]],
    indices: Sequence[int],
) -> set[int]:
    """Return indices to drop within a provided index list."""
    buckets: dict[tuple[str, ...], list[int]] = {}
    for index in indices:
        bucket = _ar_ap_dedupe_bucket_key(normalized[index])
        if bucket is None:
            continue
        buckets.setdefault(bucket, []).append(index)

    drop: set[int] = set()
    for bucket_indices in buckets.values():
        if len(bucket_indices) < 2:
            continue
        keep = min(
            bucket_indices,
            key=lambda i: (i, -_ar_ap_row_keep_score(normalized[i])),
        )
        for index in bucket_indices:
            if index != keep:
                drop.add(index)
    return drop


def dedupe_ar_ap_rows_within_file(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """
    Drop duplicate AP/AR OCR rows that share the same source file, amount, vendor, and date.
    Keeps the first row in OCR order; tie-break by row completeness score.
    """
    normalized: list[dict[str, Any]] = [dict(r) for r in rows if isinstance(r, Mapping)]
    if len(normalized) < 2:
        return normalized

    drop = _dedupe_indices_by_bucket(normalized, range(len(normalized)))
    return [row for index, row in enumerate(normalized) if index not in drop]


def remove_empty_amount_rows_per_file(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop rows with no amount when another row on the same file has an amount."""
    normalized: list[dict[str, Any]] = [dict(r) for r in rows if isinstance(r, Mapping)]
    by_file: dict[str, list[int]] = {}
    for index, row in enumerate(normalized):
        by_file.setdefault(_ar_ap_file_key(row), []).append(index)

    drop: set[int] = set()
    for file_indices in by_file.values():
        if not any(_row_has_amount(normalized[i]) for i in file_indices):
            continue
        for index in file_indices:
            if not _row_has_amount(normalized[index]):
                drop.add(index)
    return [row for index, row in enumerate(normalized) if index not in drop]


def remove_ocr_noise_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Remove header fragments and other non-transaction OCR noise."""
    normalized: list[dict[str, Any]] = [dict(r) for r in rows if isinstance(r, Mapping)]
    return [row for row in normalized if not _is_ocr_noise_row(row)]


def flag_multi_receipt_same_file(rows: list[MutableMapping[str, Any]]) -> None:
    """Flag rows on the same file that have different amounts (true multi-receipt page)."""
    by_file: dict[str, list[int]] = {}
    for index, row in enumerate(rows):
        by_file.setdefault(_ar_ap_file_key(row), []).append(index)

    for file_indices in by_file.values():
        amounts = {_normalized_amount(rows[i]) for i in file_indices if _normalized_amount(rows[i])}
        if len(amounts) < 2:
            continue
        for index in file_indices:
            row = rows[index]
            flags = list(row.get("validation_flags") or []) if isinstance(row.get("validation_flags"), list) else []
            if "multi_receipt_page" not in flags:
                flags.append("multi_receipt_page")
            row["validation_flags"] = flags
            row["needs_review"] = True


def _cross_file_dedupe_key(row: Mapping[str, Any]) -> tuple[str, ...] | None:
    amount = _normalized_amount(row)
    vendor = _normalized_vendor(row)
    date = _normalized_date(row)
    if not amount or not vendor or not date:
        return None
    return (amount, vendor, date)


def dedupe_ar_ap_rows_cross_file(
    rows: Sequence[Mapping[str, Any]],
    *,
    file_order: Sequence[str] | None = None,
    row_file_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Drop cross-file duplicates with the same vendor, amount, and date.
    Keeps the row from the earliest file in file_order.
    """
    normalized: list[dict[str, Any]] = [dict(r) for r in rows if isinstance(r, Mapping)]
    if len(normalized) < 2:
        return normalized

    order = list(file_order or [])
    rank = {file_id: idx for idx, file_id in enumerate(order)}

    def _file_rank(index: int) -> int:
        if row_file_ids and index < len(row_file_ids):
            fid = row_file_ids[index]
            if fid in rank:
                return rank[fid]
        return index

    buckets: dict[tuple[str, ...], list[int]] = {}
    for index, row in enumerate(normalized):
        key = _cross_file_dedupe_key(row)
        if key is None:
            continue
        buckets.setdefault(key, []).append(index)

    drop: set[int] = set()
    for bucket_indices in buckets.values():
        if len(bucket_indices) < 2:
            continue
        keep = min(
            bucket_indices,
            key=lambda i: (_file_rank(i), i, -_ar_ap_row_keep_score(normalized[i])),
        )
        for index in bucket_indices:
            if index != keep:
                drop.add(index)
    return [row for index, row in enumerate(normalized) if index not in drop]


def _group_rows_by_file(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        grouped.setdefault(_ar_ap_file_key(row), []).append(dict(row))
    return grouped


def clean_manager_ar_ap_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    file_order: Sequence[str] | None = None,
    row_file_ids: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Deterministic manager cleanup: per-file noise/empty/dedupe, multi-receipt flags,
    then optional cross-file dedupe.
    """
    grouped = _group_rows_by_file(rows)
    cleaned: list[dict[str, Any]] = []
    for file_rows in grouped.values():
        file_clean = remove_ocr_noise_rows(file_rows)
        file_clean = remove_empty_amount_rows_per_file(file_clean)
        file_clean = dedupe_ar_ap_rows_within_file(file_clean)
        flag_multi_receipt_same_file(file_clean)
        cleaned.extend(file_clean)

    if file_order or row_file_ids:
        cleaned = dedupe_ar_ap_rows_cross_file(
            cleaned,
            file_order=file_order,
            row_file_ids=row_file_ids,
        )
    return cleaned


def apply_batch_duplicate_flags_ar_ap(rows: list[MutableMapping[str, Any]]) -> None:
    """
    Within one batch (e.g. multi-receipt crops), flag rows with same fingerprint.
    Mutates rows in place.
    """
    if len(rows) < 2:
        return

    def _fp(r: Mapping[str, Any]) -> tuple[str, str, str]:
        d = str(r.get("date") or "").strip()
        amt = str(r.get("amount") or "").strip().replace(",", "")
        payee = str(r.get("payee") or "").strip().lower()[:40]
        return (d, amt, payee)

    buckets: dict[tuple[str, str, str], list[int]] = {}
    for i, row in enumerate(rows):
        fp = _fp(row)
        if not fp[0] and not fp[1]:
            continue
        buckets.setdefault(fp, []).append(i)

    for _fpk, indices in buckets.items():
        if len(indices) < 2:
            continue
        for i in indices:
            r = rows[i]
            flags = list(r.get("validation_flags") or []) if isinstance(r.get("validation_flags"), list) else []
            if "possible_duplicate_receipt" not in flags:
                flags.append("possible_duplicate_receipt")
            r["validation_flags"] = flags
            r["needs_review"] = True


def apply_batch_duplicate_flags_bank(rows: list[MutableMapping[str, Any]]) -> None:
    """Flag duplicate-ish bank rows within one parsed page list."""
    if len(rows) < 2:
        return

    def _key(r: Mapping[str, Any]) -> tuple[str, str, str, str]:
        d = str(
            r.get("transaction_date") or r.get("bank_date") or r.get("date") or "",
        ).strip()
        desc = str(r.get("備註") or r.get("description") or "")[:80].strip()
        dep = str(r.get("存入") or r.get("deposit") or "")
        wd = str(r.get("提取") or r.get("withdrawal") or "")
        bal = str(r.get("原幣結餘") or r.get("balance") or "")
        return (d, desc, dep or wd, bal)

    buckets: dict[tuple[str, str, str, str], list[int]] = {}
    for i, row in enumerate(rows):
        k = _key(row)
        if not k[0]:
            continue
        buckets.setdefault(k, []).append(i)

    for _k, indices in buckets.items():
        if len(indices) < 2:
            continue
        for i in indices:
            r = rows[i]
            flags = list(r.get("validation_flags") or []) if isinstance(r.get("validation_flags"), list) else []
            if "possible_duplicate_bank_row" not in flags:
                flags.append("possible_duplicate_bank_row")
            r["validation_flags"] = flags
            r["needs_review"] = True


def attach_receipt_region_provenance(
    row: MutableMapping[str, Any],
    *,
    receipt_bbox: Mapping[str, int] | None,
    pdf_page_num: int,
    parent_image_size: tuple[int, int] | None,
) -> MutableMapping[str, Any]:
    """
    Store coarse region for audit / future click-to-highlight. bbox is pixel x,y,w,h on parent image.
    """
    prev = row.get("extraction_provenance")
    prov: dict[str, Any] = dict(prev) if isinstance(prev, dict) else {}
    prov["source_pdf_page"] = pdf_page_num
    if receipt_bbox and parent_image_size:
        try:
            x = int(receipt_bbox.get("x", 0))
            y = int(receipt_bbox.get("y", 0))
            w = int(receipt_bbox.get("w", 0))
            h = int(receipt_bbox.get("h", 0))
            fw, fh = int(parent_image_size[0]), int(parent_image_size[1])
            if fw > 0 and fh > 0 and w > 0 and h > 0:
                prov["receipt_region_norm"] = {
                    "x": round(x / fw, 6),
                    "y": round(y / fh, 6),
                    "w": round(w / fw, 6),
                    "h": round(h / fh, 6),
                }
        except Exception:
            pass
    elif receipt_bbox:
        prov["receipt_bbox_pixels"] = dict(receipt_bbox)
    row["extraction_provenance"] = prov
    return row


def attach_amount_disambiguation(
    row: MutableMapping[str, Any],
    json_obj: Mapping[str, Any] | None,
    ocr_text: str,
) -> None:
    """
    For high HKD-equivalent totals, flag when normalized row amount and JSON total diverge.
    Does not modify amount fields; sets amount_disambiguation metadata for review.
    """
    _ = ocr_text
    raw_thr = os.getenv("OCR_AMOUNT_DUAL_CHECK_HKD", "50000")
    try:
        thr = Decimal(raw_thr)
    except Exception:
        thr = Decimal("50000")
    struct = _money_decimal(json_obj.get("total_amount")) if json_obj else None
    row_amt = _money_decimal(row.get("amount"))
    if struct is None or row_amt is None:
        return
    if struct <= thr:
        return
    if abs(struct - row_amt) <= Decimal("0.05"):
        return
    row["amount_disambiguation"] = {
        "structured_total": str(struct),
        "row_amount": str(row_amt),
        "needs_confirmation": True,
    }
    flags = list(row.get("validation_flags") or []) if isinstance(row.get("validation_flags"), list) else []
    if "amount_structured_row_mismatch" not in flags:
        flags.append("amount_structured_row_mismatch")
    row["validation_flags"] = flags
    row["needs_review"] = True

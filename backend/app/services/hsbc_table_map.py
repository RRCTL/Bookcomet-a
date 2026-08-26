"""HSBC Table Map: page classification, summary exclusion bands, row IDs, windows.

P0/P1 geometry-first helpers for the HSBC Business Direct V2 pipeline.
Coordinates may be PDF points or normalized 0–1; helpers document which form.
No real customer statement content lives in this module.
"""
from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal, Sequence

logger = logging.getLogger(__name__)

PageClassification = Literal[
    "portfolio_only",
    "mixed_activity_page",
    "hkdcurr_empty_section",
    "hkd_savings_activity",
    "fc_savings_activity",
    "totals_band",
    "legal_or_marketing",
    "activity_page",
    "unknown",
]

# Marker phrases (case-insensitive substring match on reconstructed lines).
_PORTFOLIO_MARKERS = (
    "portfolio summary",
    "total balance in hkd",
    "net position",
)
_TOTALS_MARKERS = (
    "total no. of deposits",
    "total no. of withdrawals",
    "total deposit amount",
    "total withdrawal amount",
    "total deposits",
    "total withdrawals",
)
_METADATA_MARKERS = (
    "exchange rate",
)
_LEGAL_MARKERS = (
    "special privileges",
    "others",
)
_OPENING_CLOSING_MARKERS = (
    "b/f balance",
    "c/f balance",
    "brought forward",
    "carried forward",
)

_SECTION_HKD_CURRENT = "HSBC Business Direct HKD Current"
_SECTION_HKD_SAVINGS = "HSBC Business Direct HKD Savings"
_SECTION_FCY = "HSBC Business Direct Foreign Currency Savings"


@dataclass
class ExclusionBand:
    y0: float
    y1: float
    reason: str
    source_text: str
    section_id: str | None = None
    confidence: float = 1.0

    def contains_y(self, y: float) -> bool:
        return self.y0 <= y <= self.y1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RowAnchor:
    row_id: str
    y: float
    amount_side: Literal["Cr", "Dr"]
    amount: float
    printed_text: str
    section_id: str | None = None
    excluded: bool = False
    exclusion_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TransactionWindow:
    window_id: str
    section_id: str | None
    expected_row_ids: list[str]
    y0: float
    y1: float
    header_context_id: str | None = None
    window_version: str = "hsbc_window_v1"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HsbcSectionMap:
    section_id: str
    label: str
    currency: str
    header_box: dict[str, float]
    table_body_box: dict[str, float]
    column_bands: dict[str, list[float]]
    summary_exclusion_bands: list[dict[str, Any]] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HsbcTableMap:
    page_id: str
    classification: PageClassification
    render_profile: str
    page_height: float
    page_width: float
    header_y: float
    exclusion_bands: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, Any]] = field(default_factory=list)
    row_anchors: list[dict[str, Any]] = field(default_factory=list)
    windows: list[dict[str, Any]] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def exclusion_enabled() -> bool:
    return _env_bool("HSBC_SUMMARY_EXCLUSION", True)


def window_vlm_enabled() -> bool:
    return _env_bool("HSBC_WINDOW_VLM", True)


def _norm_line(text: str) -> str:
    return " ".join(str(text or "").lower().split())


def _reconstruct_lines(
    words: Sequence[tuple],
    *,
    y_bucket_pt: float = 3.0,
) -> list[dict[str, Any]]:
    """Group PyMuPDF words into approximate text lines."""
    from collections import defaultdict

    buckets: dict[int, list] = defaultdict(list)
    for w in words:
        bucket = int(round(float(w[1]) / y_bucket_pt)) * int(y_bucket_pt)
        buckets[bucket].append(w)
    lines: list[dict[str, Any]] = []
    for bucket in sorted(buckets):
        ws = sorted(buckets[bucket], key=lambda ww: ww[0])
        text = " ".join(str(ww[4]).strip() for ww in ws if str(ww[4]).strip())
        if not text:
            continue
        y0 = min(float(ww[1]) for ww in ws)
        y1 = max(float(ww[3]) for ww in ws)
        lines.append({"y0": y0, "y1": y1, "text": text, "norm": _norm_line(text)})
    return lines


def find_marker_lines(
    words: Sequence[tuple],
    markers: Iterable[str],
) -> list[dict[str, Any]]:
    """Return lines whose normalized text contains any marker substring."""
    markers_l = [m.lower() for m in markers]
    out: list[dict[str, Any]] = []
    for line in _reconstruct_lines(words):
        for m in markers_l:
            if m in line["norm"]:
                out.append({**line, "marker": m})
                break
    return out


def build_exclusion_bands(
    words: Sequence[tuple],
    *,
    page_height: float,
    header_y: float | None = None,
    section_ys: Sequence[float] | None = None,
) -> list[ExclusionBand]:
    """Build y-exclusion bands for summary / legal / metadata content.

    Band extents (PDF points):
    - Portfolio / account overview: page top → activity header (or mid-page fallback)
    - Section totals: marker y → next section header or +36pt
    - Metadata / legal: marker y → next strong section or page end
    - Opening/closing labels: tight ±10pt band (metadata; amount filter still applied)
    """
    bands: list[ExclusionBand] = []
    section_ys_sorted = sorted(float(y) for y in (section_ys or []))
    hy = float(header_y) if header_y is not None else None

    def _next_section_after(y: float) -> float | None:
        for sy in section_ys_sorted:
            if sy > y + 2.0:
                return sy
        return None

    # Portfolio: from top to activity table header
    for hit in find_marker_lines(words, _PORTFOLIO_MARKERS):
        y1 = hy - 2.0 if hy is not None and hy > hit["y0"] else min(
            page_height * 0.55, hit["y1"] + 80.0
        )
        bands.append(
            ExclusionBand(
                y0=0.0,
                y1=max(y1, hit["y1"] + 4.0),
                reason="portfolio_overview",
                source_text=hit["text"][:120],
                confidence=0.95,
            )
        )

    for hit in find_marker_lines(words, _TOTALS_MARKERS):
        nxt = _next_section_after(hit["y0"])
        y1 = (nxt - 2.0) if nxt is not None else min(page_height, hit["y1"] + 36.0)
        bands.append(
            ExclusionBand(
                y0=max(0.0, hit["y0"] - 4.0),
                y1=y1,
                reason="section_totals",
                source_text=hit["text"][:120],
                confidence=1.0,
            )
        )

    for hit in find_marker_lines(words, _METADATA_MARKERS):
        nxt = _next_section_after(hit["y0"])
        y1 = (nxt - 2.0) if nxt is not None else min(page_height, hit["y1"] + 48.0)
        bands.append(
            ExclusionBand(
                y0=max(0.0, hit["y0"] - 4.0),
                y1=y1,
                reason="exchange_rate_metadata",
                source_text=hit["text"][:120],
                confidence=0.9,
            )
        )

    for hit in find_marker_lines(words, _LEGAL_MARKERS):
        # "Others" is a short word — require line start / section-like context
        if hit["marker"] == "others":
            norm = hit["norm"]
            if not (
                norm == "others"
                or norm.startswith("others ")
                or " others" in f" {norm}"
            ):
                continue
        bands.append(
            ExclusionBand(
                y0=max(0.0, hit["y0"] - 4.0),
                y1=page_height,
                reason="legal_or_marketing",
                source_text=hit["text"][:120],
                confidence=0.9,
            )
        )

    for hit in find_marker_lines(words, _OPENING_CLOSING_MARKERS):
        bands.append(
            ExclusionBand(
                y0=max(0.0, hit["y0"] - 10.0),
                y1=min(page_height, hit["y1"] + 10.0),
                reason="opening_closing_metadata",
                source_text=hit["text"][:120],
                confidence=0.85,
            )
        )

    bands.sort(key=lambda b: (b.y0, b.y1))
    return _merge_overlapping_bands(bands)


def _merge_overlapping_bands(bands: list[ExclusionBand]) -> list[ExclusionBand]:
    """Merge overlapping bands that share the same reason."""
    if not bands:
        return []
    by_reason: dict[str, list[ExclusionBand]] = {}
    for b in bands:
        by_reason.setdefault(b.reason, []).append(b)
    merged: list[ExclusionBand] = []
    for reason, group in by_reason.items():
        group.sort(key=lambda b: b.y0)
        cur = group[0]
        for nxt in group[1:]:
            if nxt.y0 <= cur.y1 + 2.0:
                cur = ExclusionBand(
                    y0=cur.y0,
                    y1=max(cur.y1, nxt.y1),
                    reason=reason,
                    source_text=cur.source_text,
                    section_id=cur.section_id or nxt.section_id,
                    confidence=max(cur.confidence, nxt.confidence),
                )
            else:
                merged.append(cur)
                cur = nxt
        merged.append(cur)
    merged.sort(key=lambda b: (b.y0, b.y1))
    return merged


def y_in_exclusion_bands(y: float, bands: Sequence[ExclusionBand]) -> ExclusionBand | None:
    for b in bands:
        if b.contains_y(y):
            return b
    return None


def filter_amounts_outside_exclusion(
    amounts: Sequence[dict[str, Any]],
    bands: Sequence[ExclusionBand],
    *,
    also_exclude_reasons: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split amounts into eligible vs excluded.

    Opening/closing metadata bands exclude Cr/Dr anchors that sit on B/F/C/F
    label lines (rare); balance-only B/F handling remains in the V2 BF helper.
    """
    reasons = set(also_exclude_reasons or ())
    # Always exclude these from transaction amount anchors
    reasons.update(
        {
            "portfolio_overview",
            "section_totals",
            "exchange_rate_metadata",
            "legal_or_marketing",
            "opening_closing_metadata",
        }
    )
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for amt in amounts:
        y = float(amt.get("y", 0.0))
        hit = y_in_exclusion_bands(y, bands)
        if hit is not None and hit.reason in reasons:
            dropped.append({**amt, "_exclusion_reason": hit.reason, "_exclusion_text": hit.source_text})
        else:
            kept.append(dict(amt))
    return kept, dropped


def classify_hsbc_page(
    *,
    no_table: bool,
    words: Sequence[tuple] | None = None,
    page_text: str | None = None,
    sections: Sequence[dict[str, Any]] | None = None,
    amounts: Sequence[dict[str, Any]] | None = None,
    exclusion_bands: Sequence[ExclusionBand] | None = None,
) -> PageClassification:
    """Content/geometry page classification (not page-number based)."""
    text = _norm_line(page_text or "")
    if words and not text:
        text = _norm_line(" ".join(str(w[4]) for w in words[:200]))

    bands = list(exclusion_bands or [])
    has_legal = any(b.reason == "legal_or_marketing" for b in bands) or any(
        m in text for m in _LEGAL_MARKERS
    )
    has_portfolio = any(b.reason == "portfolio_overview" for b in bands) or any(
        m in text for m in _PORTFOLIO_MARKERS
    )
    has_totals = any(b.reason == "section_totals" for b in bands) or any(
        m in text for m in _TOTALS_MARKERS
    )
    sec_headers = [str(s.get("header", "")) for s in (sections or [])]
    amt_n = len(amounts or [])

    if no_table:
        if has_legal and not has_portfolio:
            return "legal_or_marketing"
        if has_portfolio:
            return "portfolio_only"
        if has_legal:
            return "legal_or_marketing"
        return "unknown"

    if has_portfolio and amt_n > 0:
        return "mixed_activity_page"

    if has_totals and amt_n == 0:
        return "totals_band"

    if _SECTION_FCY in sec_headers and not any(
        h in (_SECTION_HKD_CURRENT, _SECTION_HKD_SAVINGS) for h in sec_headers
    ):
        return "fc_savings_activity"

    if _SECTION_HKD_CURRENT in sec_headers and amt_n == 0:
        return "hkdcurr_empty_section"

    if _SECTION_HKD_SAVINGS in sec_headers:
        return "hkd_savings_activity"

    if amt_n > 0:
        return "activity_page"
    return "unknown"


def stable_row_id(
    *,
    page_number: int,
    section_id: str | None,
    anchor_y: float,
    amount_side: str,
    printed_amount: float | str,
) -> str:
    """Stable SHA-256 row id (hex digest) across crop/VLM/retry."""
    try:
        amt = f"{float(str(printed_amount).replace(',', '')):.2f}"
    except Exception:
        amt = str(printed_amount)
    payload = (
        f"{int(page_number)}|{section_id or ''}|{round(float(anchor_y), 1)}|"
        f"{amount_side}|{amt}"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _section_id_for_y(y: float, sections: Sequence[dict[str, Any]]) -> str | None:
    chosen = None
    for s in sections:
        if float(s.get("y", 0.0)) <= y:
            header = str(s.get("header", ""))
            chosen = re.sub(r"[^a-z0-9]+", "-", header.lower()).strip("-") or None
        else:
            break
    return chosen


def build_row_anchors(
    amounts: Sequence[dict[str, Any]],
    *,
    page_number: int,
    sections: Sequence[dict[str, Any]] | None = None,
    excluded_amounts: Sequence[dict[str, Any]] | None = None,
) -> list[RowAnchor]:
    anchors: list[RowAnchor] = []
    secs = list(sections or [])
    for amt in amounts:
        y = float(amt["y"])
        side = str(amt.get("col", "Cr"))
        if side not in ("Cr", "Dr"):
            side = "Cr"
        printed = amt.get("text", amt.get("amount", ""))
        sid = _section_id_for_y(y, secs)
        rid = stable_row_id(
            page_number=page_number,
            section_id=sid,
            anchor_y=y,
            amount_side=side,
            printed_amount=printed if printed != "" else amt.get("amount", 0),
        )
        anchors.append(
            RowAnchor(
                row_id=rid,
                y=y,
                amount_side=side,  # type: ignore[arg-type]
                amount=float(amt["amount"]),
                printed_text=str(printed),
                section_id=sid,
                excluded=False,
            )
        )
    for amt in excluded_amounts or []:
        y = float(amt["y"])
        side = str(amt.get("col", "Cr"))
        if side not in ("Cr", "Dr"):
            side = "Cr"
        printed = amt.get("text", amt.get("amount", ""))
        sid = _section_id_for_y(y, secs)
        rid = stable_row_id(
            page_number=page_number,
            section_id=sid,
            anchor_y=y,
            amount_side=side,
            printed_amount=printed if printed != "" else amt.get("amount", 0),
        )
        anchors.append(
            RowAnchor(
                row_id=rid,
                y=y,
                amount_side=side,  # type: ignore[arg-type]
                amount=float(amt["amount"]),
                printed_text=str(printed),
                section_id=sid,
                excluded=True,
                exclusion_reason=str(amt.get("_exclusion_reason") or "excluded"),
            )
        )
    anchors.sort(key=lambda a: (a.y, a.row_id))
    return anchors


def plan_transaction_windows(
    row_anchors: Sequence[RowAnchor],
    *,
    page_height: float,
    target_rows: int = 6,
    min_rows: int = 4,
    max_rows: int = 10,
    row_pad_pt: float = 14.0,
    preserve_date_groups: bool = True,
    date_label_ys: Sequence[float] | None = None,
) -> list[TransactionWindow]:
    """Group eligible (non-excluded) row anchors into adaptive VLM windows.

    Windows never cross page bounds. Near totals / page bottom, windows end early
    rather than padding to a fixed count.
    """
    eligible = [a for a in row_anchors if not a.excluded]
    if not eligible:
        return []

    target_rows = max(1, int(os.getenv("HSBC_WINDOW_TARGET_ROWS", str(target_rows))))
    min_rows = max(1, int(os.getenv("HSBC_WINDOW_MIN_ROWS", str(min_rows))))
    max_rows = max(min_rows, int(os.getenv("HSBC_WINDOW_MAX_ROWS", str(max_rows))))
    if target_rows < min_rows:
        target_rows = min_rows
    if target_rows > max_rows:
        target_rows = max_rows

    date_ys = sorted(float(y) for y in (date_label_ys or []))
    windows: list[TransactionWindow] = []
    i = 0
    win_idx = 0
    while i < len(eligible):
        # Prefer target_rows; allow up to max_rows; allow shorter near end
        end = min(i + target_rows, len(eligible))
        # Extend toward max_rows if next rows are densely packed (same section)
        while end < len(eligible) and (end - i) < max_rows:
            if eligible[end].section_id != eligible[i].section_id:
                break
            # Stop before a large vertical gap (section/totals break)
            gap = eligible[end].y - eligible[end - 1].y
            if gap > 40.0:
                break
            end += 1
        # Shrink to avoid splitting a date group awkwardly when possible
        if preserve_date_groups and date_ys and (end - i) > min_rows:
            # If a date label sits just after the last included row, keep window end
            pass
        chunk = eligible[i:end]
        # Avoid undersized middle windows when enough remain for min_rows
        if len(chunk) < min_rows and end < len(eligible):
            end = min(i + min_rows, len(eligible))
            chunk = eligible[i:end]

        y0 = max(0.0, chunk[0].y - row_pad_pt)
        y1 = min(page_height, chunk[-1].y + row_pad_pt + 18.0)
        sid = chunk[0].section_id
        win_idx += 1
        windows.append(
            TransactionWindow(
                window_id=f"w{win_idx}:{sid or 'sec'}:{round(y0, 1)}-{round(y1, 1)}",
                section_id=sid,
                expected_row_ids=[a.row_id for a in chunk],
                y0=y0,
                y1=y1,
                header_context_id=f"hdr:{sid or 'page'}",
            )
        )
        i = end
    return windows


def assess_page_quality(
    img_bgr: Any | None = None,
    *,
    has_text_layer: bool = True,
    description_fill_rate: float | None = None,
) -> dict[str, Any]:
    """Lightweight quality signals for HSBC render/VLM routing.

    Never invents numeric evidence. Returns a render_profile suggestion and flags.
    """
    signals: dict[str, Any] = {
        "has_text_layer": bool(has_text_layer),
        "description_fill_rate": description_fill_rate,
        "blur_score": None,
        "needs_review": False,
        "recapture_requested": False,
        "enhancement_recipe": None,
        "render_profile": "digital_text_pdf_v1",
    }
    if description_fill_rate is not None and description_fill_rate < 0.50:
        signals["render_profile"] = "scan_300dpi_clahe_v1"
        signals["enhancement_recipe"] = "clahe_light"
    if img_bgr is not None:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore

            gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
            blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            signals["blur_score"] = blur
            if blur < 40.0:
                signals["needs_review"] = True
                signals["render_profile"] = "scan_low_sharpness_review_v1"
            # Illumination variance (shadow heuristic)
            illum = float(np.std(gray.astype("float64")))
            signals["illumination_std"] = illum
            if illum > 55.0 and description_fill_rate is not None and description_fill_rate < 0.5:
                signals["enhancement_recipe"] = "clahe_light"
        except Exception as exc:
            logger.debug("[HSBC-QUALITY] probe skipped: %s", exc)
    return signals


def apply_reversible_enhancement(img_bgr: Any, recipe: str | None) -> Any:
    """Return enhanced BGR image; never mutates caller-owned arrays in place."""
    if img_bgr is None or not recipe:
        return img_bgr
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        out = img_bgr.copy()
        if recipe == "clahe_light":
            lab = cv2.cvtColor(out, cv2.COLOR_BGR2LAB)
            l, a, b = cv2.split(lab)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            l2 = clahe.apply(l)
            out = cv2.cvtColor(cv2.merge([l2, a, b]), cv2.COLOR_LAB2BGR)
        return out
    except Exception as exc:
        logger.warning("[HSBC-QUALITY] enhancement failed (%s); using original", exc)
        return img_bgr


def crop_window_bgr(
    img_bgr: Any,
    *,
    y0_pt: float,
    y1_pt: float,
    render_scale: float,
    page_width_pt: float,
    header_y_pt: float | None = None,
    x0_frac: float = 0.04,
    x1_frac: float = 0.98,
    include_header: bool = True,
) -> Any:
    """Crop a full-table-width window (optionally with inherited header strip)."""
    import numpy as np  # type: ignore

    h, w = img_bgr.shape[:2]
    x0 = int(max(0, page_width_pt * x0_frac * render_scale))
    x1 = int(min(w, page_width_pt * x1_frac * render_scale))
    y0 = float(y0_pt)
    if include_header and header_y_pt is not None:
        # Prepend a thin header context strip when window is below the header
        if y0 > float(header_y_pt) + 8.0:
            # Caller may stitch; for single crop we expand upward slightly only
            y0 = max(float(header_y_pt) - 4.0, y0 - 24.0)
    py0 = int(max(0, math.floor(y0 * render_scale)))
    py1 = int(min(h, math.ceil(float(y1_pt) * render_scale)))
    if py1 <= py0 + 2:
        py1 = min(h, py0 + 4)
    crop = img_bgr[py0:py1, x0:x1]
    if crop.size == 0:
        return img_bgr
    return np.ascontiguousarray(crop)


def _norm_box(x0: float, y0: float, x1: float, y1: float, w: float, h: float) -> dict[str, float]:
    if w <= 0 or h <= 0:
        return {"x0": 0.0, "y0": 0.0, "x1": 1.0, "y1": 1.0}
    return {
        "x0": max(0.0, min(1.0, x0 / w)),
        "y0": max(0.0, min(1.0, y0 / h)),
        "x1": max(0.0, min(1.0, x1 / w)),
        "y1": max(0.0, min(1.0, y1 / h)),
    }


def build_hsbc_table_map(
    *,
    page_number: int,
    page_width: float,
    page_height: float,
    classification: PageClassification,
    header_y: float,
    dep_hdr_x: float,
    wdw_hdr_x: float,
    bal_hdr_x: float,
    sections: Sequence[dict[str, Any]],
    exclusion_bands: Sequence[ExclusionBand],
    row_anchors: Sequence[RowAnchor],
    windows: Sequence[TransactionWindow] | None = None,
    quality: dict[str, Any] | None = None,
    document_id: str | None = None,
) -> HsbcTableMap:
    """Assemble the formal HsbcTableMap intermediate model."""
    q = quality or {}
    render_profile = str(q.get("render_profile") or "digital_text_pdf_v1")
    page_id = f"document:{document_id or 'local'}:page:{page_number}"
    w, h = float(page_width), float(page_height)

    # Column bands as normalized mid±delta approximations
    def _band(cx: float, half: float = 0.06) -> list[float]:
        return [
            max(0.0, (cx / w) - half),
            min(1.0, (cx / w) + half),
        ]

    col_bands = {
        "date": [0.04, 0.14],
        "description": [0.14, max(0.2, (dep_hdr_x / w) - 0.06)],
        "deposit": _band(dep_hdr_x),
        "withdrawal": _band(wdw_hdr_x),
        "balance": _band(bal_hdr_x, 0.07),
    }

    band_dicts = [b.to_dict() for b in exclusion_bands]
    # Normalize exclusion band y to 0–1 for the map payload (keep pt in parallel keys)
    for bd in band_dicts:
        bd["y0_norm"] = bd["y0"] / h if h else 0.0
        bd["y1_norm"] = bd["y1"] / h if h else 0.0

    section_maps: list[dict[str, Any]] = []
    sorted_secs = sorted(sections or [], key=lambda s: float(s.get("y", 0.0)))
    for idx, sec in enumerate(sorted_secs):
        sy = float(sec.get("y", 0.0))
        label = str(sec.get("header", ""))
        sid = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or f"sec-{idx}"
        y_next = (
            float(sorted_secs[idx + 1]["y"])
            if idx + 1 < len(sorted_secs)
            else h
        )
        currency = "FCY" if "Foreign Currency" in label else "HKD"
        body_y0 = max(sy, float(header_y or sy))
        # Clip body end at first overlapping exclusion of totals/legal below
        body_y1 = y_next
        for b in exclusion_bands:
            if b.reason in {"section_totals", "legal_or_marketing"} and b.y0 > body_y0:
                body_y1 = min(body_y1, b.y0)
        sec_excl = [
            {
                "y0": max(0.0, b["y0_norm"]),
                "y1": min(1.0, b["y1_norm"]),
                "reason": b["reason"],
            }
            for b in band_dicts
            if float(b["y0"]) < body_y1 and float(b["y1"]) > body_y0
        ]
        sec_rows = [
            a.to_dict()
            for a in row_anchors
            if (a.section_id == sid or a.section_id is None)
            and body_y0 - 5.0 <= a.y <= body_y1 + 5.0
        ]
        section_maps.append(
            HsbcSectionMap(
                section_id=sid,
                label=label,
                currency=currency,
                header_box=_norm_box(0.05 * w, sy - 6.0, 0.96 * w, sy + 18.0, w, h),
                table_body_box=_norm_box(0.05 * w, body_y0, 0.96 * w, body_y1, w, h),
                column_bands=col_bands,
                summary_exclusion_bands=sec_excl,
                rows=sec_rows,
            ).to_dict()
        )

    return HsbcTableMap(
        page_id=page_id,
        classification=classification,
        render_profile=render_profile,
        page_height=h,
        page_width=w,
        header_y=float(header_y or 0.0),
        exclusion_bands=band_dicts,
        sections=section_maps,
        row_anchors=[a.to_dict() for a in row_anchors],
        windows=[w_.to_dict() for w_ in (windows or [])],
        quality=q,
    )


def enrich_prescan_with_table_map(
    ps: dict[str, Any],
    *,
    words: Sequence[tuple],
    page_number: int,
    page_text: str | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    """Apply P0 exclusion + classification + table map onto a prescan dict.

    Mutates a shallow copy of ``ps`` and returns it. When
    ``HSBC_SUMMARY_EXCLUSION`` is false, amounts are left unchanged but the map
    is still attached for observability.
    """
    out = dict(ps)
    page_height = float(out.get("page_height") or 1.0)
    page_width = float(out.get("page_width") or out.get("dep_hdr_x", 500) / 0.64)
    # Prefer explicit width if caller set it
    if "page_width" not in out:
        out["page_width"] = page_width

    bands = build_exclusion_bands(
        words,
        page_height=page_height,
        header_y=float(out.get("header_y") or 0.0) or None,
        section_ys=[float(s.get("y", 0.0)) for s in (out.get("sections") or [])],
    )
    raw_amounts = list(out.get("amounts") or [])
    if exclusion_enabled() and not out.get("no_table"):
        kept, dropped = filter_amounts_outside_exclusion(raw_amounts, bands)
        if dropped:
            logger.info(
                "[HSBC-TABLEMAP][P%d] Excluded %d summary/legal amount anchor(s): %s",
                page_number,
                len(dropped),
                ", ".join(
                    f"{d.get('text') or d.get('amount')}@{d.get('y')}({d.get('_exclusion_reason')})"
                    for d in dropped[:8]
                ),
            )
        out["amounts"] = kept
        out["excluded_amounts"] = dropped
    else:
        out["excluded_amounts"] = []

    classification = classify_hsbc_page(
        no_table=bool(out.get("no_table")),
        words=words,
        page_text=page_text,
        sections=out.get("sections") or [],
        amounts=out.get("amounts") or [],
        exclusion_bands=bands,
    )
    out["classification"] = classification
    out["exclusion_bands"] = [b.to_dict() for b in bands]

    anchors = build_row_anchors(
        out.get("amounts") or [],
        page_number=page_number,
        sections=out.get("sections") or [],
        excluded_amounts=out.get("excluded_amounts") or [],
    )
    windows = plan_transaction_windows(
        anchors,
        page_height=page_height,
        date_label_ys=[float(d.get("y", 0.0)) for d in (out.get("date_labels") or [])],
    )
    quality = assess_page_quality(
        has_text_layer=not bool(out.get("no_table")),
        description_fill_rate=None,
    )
    table_map = build_hsbc_table_map(
        page_number=page_number,
        page_width=page_width,
        page_height=page_height,
        classification=classification,
        header_y=float(out.get("header_y") or 0.0),
        dep_hdr_x=float(out.get("dep_hdr_x") or page_width * 0.64),
        wdw_hdr_x=float(out.get("wdw_hdr_x") or page_width * 0.76),
        bal_hdr_x=float(out.get("bal_hdr_x") or page_width * 0.88),
        sections=out.get("sections") or [],
        exclusion_bands=bands,
        row_anchors=anchors,
        windows=windows,
        quality=quality,
        document_id=document_id,
    )
    out["row_anchors"] = [a.to_dict() for a in anchors]
    out["windows"] = [w.to_dict() for w in windows]
    out["table_map"] = table_map.to_dict()
    return out

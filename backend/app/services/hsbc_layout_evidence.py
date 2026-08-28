"""HSBC layout evidence (Slice C) — geometry before finance.

Builds row anchors and numeric tokens from word boxes (native PDF text or
local OCR textpage). Free-form VLM amounts are never admitted here.

Synthetic / geometry fixtures only in tests — no real statement content.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

logger = logging.getLogger(__name__)

AMOUNT_RE = re.compile(
    r"^\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?$"
    r"|^\d{4,}(?:\.\d{1,2})?$"
    r"|^\d+\.\d{2}$"
)

AmountSide = Literal["Cr", "Dr", "Bal"]


@dataclass
class LayoutToken:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    block: int = 0
    line: int = 0
    word: int = 0

    @property
    def x_mid(self) -> float:
        return (self.x0 + self.x1) / 2.0

    @property
    def y_mid(self) -> float:
        return (self.y0 + self.y1) / 2.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "x0": self.x0,
            "y0": self.y0,
            "x1": self.x1,
            "y1": self.y1,
        }


@dataclass
class ColumnBands:
    deposit_x: float | None = None
    withdrawal_x: float | None = None
    balance_x: float | None = None
    header_y: float | None = None

    @property
    def ok(self) -> bool:
        return self.deposit_x is not None or self.withdrawal_x is not None


@dataclass
class EvidenceAnchor:
    row_anchor_id: str
    y: float
    side: AmountSide
    amount: float
    printed_text: str
    token: LayoutToken
    section_id: str | None = None

    def to_candidate(self) -> dict[str, Any]:
        dep = self.amount if self.side == "Cr" else None
        wdw = self.amount if self.side == "Dr" else None
        bal = self.amount if self.side == "Bal" else None
        prov = {
            "deposit": "layout_cr" if self.side == "Cr" else None,
            "withdrawal": "layout_dr" if self.side == "Dr" else None,
            "balance": "layout_balance_band" if self.side == "Bal" else None,
        }
        # Map layout roles to admission-recognised provenance labels.
        col_prov = {
            "deposit": "prescan_cr" if self.side == "Cr" else None,
            "withdrawal": "prescan_dr" if self.side == "Dr" else None,
            "balance": "prescan_balance_band" if self.side == "Bal" else None,
        }
        kind = "balance_snapshot" if self.side == "Bal" else "transaction"
        return {
            "row_anchor_id": self.row_anchor_id,
            "_hsbc_row_id": self.row_anchor_id,
            "deposit": dep,
            "withdrawal": wdw,
            "balance": bal,
            "description": "",
            "numeric_token_ids": [f"{self.row_anchor_id}:{self.side}:{self.printed_text}"],
            "column_provenance": col_prov,
            "row_kind": kind,
            "has_balance_band_token": self.side == "Bal",
            "layout_provenance": prov,
            "section_id": self.section_id,
            "exportable": True,
        }


def parse_amount(text: str) -> float | None:
    raw = (text or "").strip().replace(",", "")
    if not AMOUNT_RE.match((text or "").strip()):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def tokens_from_pymupdf_words(words: Sequence[Sequence[Any]]) -> list[LayoutToken]:
    out: list[LayoutToken] = []
    for w in words or []:
        if len(w) < 5:
            continue
        text = str(w[4]).strip()
        if not text:
            continue
        out.append(
            LayoutToken(
                text=text,
                x0=float(w[0]),
                y0=float(w[1]),
                x1=float(w[2]),
                y1=float(w[3]),
                block=int(w[5]) if len(w) > 5 else 0,
                line=int(w[6]) if len(w) > 6 else 0,
                word=int(w[7]) if len(w) > 7 else 0,
            )
        )
    return out


def detect_column_bands(tokens: Sequence[LayoutToken], page_width: float) -> ColumnBands:
    bands = ColumnBands()
    for t in tokens:
        low = t.text.lower()
        if low == "deposit" and bands.deposit_x is None:
            bands.deposit_x = t.x_mid
            bands.header_y = t.y0
        elif low == "withdrawal" and bands.withdrawal_x is None:
            bands.withdrawal_x = t.x_mid
            if bands.header_y is None:
                bands.header_y = t.y0
        elif low == "balance" and bands.balance_x is None:
            bands.balance_x = t.x_mid
            if bands.header_y is None:
                bands.header_y = t.y0
    # Fallback ratios typical of HSBC A4 — geometry defaults only, not page rules.
    if bands.deposit_x is None:
        bands.deposit_x = page_width * 0.64
    if bands.withdrawal_x is None:
        bands.withdrawal_x = page_width * 0.76
    if bands.balance_x is None:
        bands.balance_x = page_width * 0.88
    return bands


def _nearest_side(x_mid: float, bands: ColumnBands) -> AmountSide | None:
    candidates: list[tuple[float, AmountSide]] = []
    if bands.deposit_x is not None:
        candidates.append((abs(x_mid - bands.deposit_x), "Cr"))
    if bands.withdrawal_x is not None:
        candidates.append((abs(x_mid - bands.withdrawal_x), "Dr"))
    if bands.balance_x is not None:
        candidates.append((abs(x_mid - bands.balance_x), "Bal"))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    dist, side = candidates[0]
    # Reject if too far from any column centre (~45pt window).
    if dist > 45.0:
        return None
    # Prefer amount columns over balance when distances are close.
    if side == "Bal" and len(candidates) > 1 and candidates[1][0] < 30.0:
        return candidates[1][1]
    return side


def build_amount_anchors(
    tokens: Sequence[LayoutToken],
    bands: ColumnBands,
    *,
    page_index_1based: int = 1,
) -> list[EvidenceAnchor]:
    min_y = (bands.header_y or 0.0) + 4.0
    anchors: list[EvidenceAnchor] = []
    for i, t in enumerate(tokens):
        if t.y0 < min_y:
            continue
        amt = parse_amount(t.text)
        if amt is None:
            continue
        side = _nearest_side(t.x_mid, bands)
        if side is None:
            continue
        if side == "Bal":
            # Balance-only tokens are snapshots; keep for BF / empty sections,
            # not ordinary dual-side admission.
            pass
        rid = f"layout-p{page_index_1based}-a{i}-{side}"
        anchors.append(
            EvidenceAnchor(
                row_anchor_id=rid,
                y=t.y_mid,
                side=side,
                amount=amt,
                printed_text=t.text,
                token=t,
            )
        )
    anchors.sort(key=lambda a: a.y)
    return anchors


def merge_cr_dr_same_row(
    anchors: Sequence[EvidenceAnchor],
    *,
    y_tol: float = 8.0,
) -> list[dict[str, Any]]:
    """One candidate per physical y-band: Cr or Dr (not both); Bal attaches."""
    used: set[str] = set()
    candidates: list[dict[str, Any]] = []
    amount_anchors = [a for a in anchors if a.side in {"Cr", "Dr"}]
    bal_anchors = [a for a in anchors if a.side == "Bal"]

    for a in amount_anchors:
        if a.row_anchor_id in used:
            continue
        # Dual amount on same y → unresolved later via admission (emit both sides fail).
        peers = [
            b
            for b in amount_anchors
            if b.row_anchor_id not in used and abs(b.y - a.y) <= y_tol
        ]
        if len(peers) > 1 and {p.side for p in peers} == {"Cr", "Dr"}:
            # Keep first as dual-amount bad candidate for unresolved path.
            dual = dict(a.to_candidate())
            other = next(p for p in peers if p.side != a.side)
            dual["deposit"] = a.amount if a.side == "Cr" else other.amount
            dual["withdrawal"] = a.amount if a.side == "Dr" else other.amount
            dual["column_provenance"] = {
                "deposit": "prescan_cr",
                "withdrawal": "prescan_dr",
                "balance": None,
            }
            dual["numeric_token_ids"] = [
                f"{a.row_anchor_id}:Cr",
                f"{other.row_anchor_id}:Dr",
            ]
            for p in peers:
                used.add(p.row_anchor_id)
            candidates.append(dual)
            continue
        cand = a.to_candidate()
        # Attach nearest balance in band.
        bals = [b for b in bal_anchors if abs(b.y - a.y) <= y_tol]
        if bals:
            b0 = min(bals, key=lambda b: abs(b.y - a.y))
            cand["balance"] = b0.amount
            cand["has_balance_band_token"] = True
            cand["column_provenance"]["balance"] = "prescan_balance_band"
            cand["numeric_token_ids"] = list(cand["numeric_token_ids"]) + [
                f"{b0.row_anchor_id}:Bal:{b0.printed_text}"
            ]
            used.add(b0.row_anchor_id)
        used.add(a.row_anchor_id)
        candidates.append(cand)
    return candidates


def extract_words_from_page(page: Any, *, force_ocr: bool = False) -> list[LayoutToken]:
    """Native words first; optional OCR textpage when sparse (scanned)."""
    words = page.get_text("words") or []
    tokens = tokens_from_pymupdf_words(words)
    threshold = int(os.getenv("HSBC_LAYOUT_OCR_WORD_THRESHOLD", "20") or "20")
    want_ocr = force_ocr or len(tokens) < threshold
    if not want_ocr:
        return tokens
    if os.getenv("HSBC_LAYOUT_OCR", "true").lower() not in ("1", "true", "yes"):
        return tokens
    try:
        tp = page.get_textpage_ocr(dpi=int(os.getenv("HSBC_LAYOUT_OCR_DPI", "200") or "200"))
        ocr_words = page.get_text("words", textpage=tp) or []
        ocr_tokens = tokens_from_pymupdf_words(ocr_words)
        if len(ocr_tokens) > len(tokens):
            logger.info(
                "[HSBC-LAYOUT] OCR textpage tokens=%d (native=%d)",
                len(ocr_tokens),
                len(tokens),
            )
            return ocr_tokens
    except Exception as exc:
        logger.warning("[HSBC-LAYOUT] OCR textpage unavailable: %s", exc)
    return tokens


def build_page_candidates_from_layout(
    page: Any,
    *,
    page_index_1based: int = 1,
    force_ocr: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return (candidates, meta) for admission."""
    page_width = float(page.rect.width)
    tokens = extract_words_from_page(page, force_ocr=force_ocr)
    bands = detect_column_bands(tokens, page_width)
    anchors = build_amount_anchors(tokens, bands, page_index_1based=page_index_1based)
    # Ordinary txn candidates exclude pure Bal-only rows unless no Cr/Dr.
    cr_dr = [a for a in anchors if a.side in {"Cr", "Dr"}]
    candidates = merge_cr_dr_same_row(cr_dr + [a for a in anchors if a.side == "Bal"])
    meta = {
        "token_count": len(tokens),
        "anchor_count": len(anchors),
        "amount_anchor_count": len(cr_dr),
        "bands_ok": bands.ok,
        "header_y": bands.header_y,
    }
    return candidates, meta

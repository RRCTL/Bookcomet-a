"""
Independent OCR cross-check for AP rows.

Re-reads a crop / page image with a local OCR engine (PaddleOCR today, a cloud
reader in future) and flags VLM-extracted fields (amount, currency, date,
merchant) that disagree with the independent reading. Flag-only: it never
overwrites values, it only attaches ocr_xcheck_* validation flags.

This path is intentionally independent of OcrService (whose registry redirects
the removed "paddle" provider back to the VLM). It is disabled unless
AP_OCR_CROSS_CHECK_PROVIDER is set.
"""
from __future__ import annotations

import logging
import re
from typing import Mapping, Protocol, runtime_checkable

from app.core.config import settings
from app.services.extraction_validation import ValidationResult

logger = logging.getLogger(__name__)


@runtime_checkable
class LocalOcrReaderPort(Protocol):
    """Adapter interface: one implementation per independent OCR engine."""

    name: str

    def is_available(self) -> bool:
        """Whether read_text can be attempted (models/deps loaded)."""
        ...

    def read_text(self, image_path: str) -> str:
        """Return plain recognized text for the image (best-effort, may be empty)."""
        ...


class PaddleOcrReader:
    """Local PaddleOCR reader. Lazy-imports paddleocr so a missing dependency
    cannot break module import; degrades to unavailable on any import error."""

    name = "paddle"

    def __init__(self) -> None:
        self._engine = None
        self._available = False
        try:
            from paddleocr import PaddleOCR

            self._engine = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
            self._available = True
        except Exception as exc:  # ImportError or runtime init (oneDNN/PIR on Windows)
            logger.warning("[ocr_xcheck] PaddleOCR unavailable: %s", exc)
            self._engine = None
            self._available = False

    def is_available(self) -> bool:
        return self._available

    def read_text(self, image_path: str) -> str:
        if not self._available or self._engine is None:
            return ""
        try:
            raw = self._engine.ocr(image_path, cls=True)
        except Exception as exc:
            logger.warning("[ocr_xcheck] PaddleOCR read failed for %s: %s", image_path, exc)
            return ""
        return _flatten_paddle_result(raw)


def _flatten_paddle_result(raw: object) -> str:
    """Best-effort extraction of recognized strings from PaddleOCR output.

    Tolerates the common nested shape [[ [box, (text, conf)], ... ]] across
    paddleocr versions; ignores anything that does not look like a text tuple.
    """
    texts: list[str] = []

    def _walk(node: object) -> None:
        if isinstance(node, str):
            texts.append(node)
            return
        if isinstance(node, (list, tuple)):
            # A recognition entry is typically (text, confidence).
            if (
                len(node) == 2
                and isinstance(node[0], str)
                and isinstance(node[1], (int, float))
            ):
                texts.append(node[0])
                return
            for child in node:
                _walk(child)

    _walk(raw)
    return "\n".join(t for t in texts if t and t.strip())


_reader_cache: tuple[str, LocalOcrReaderPort | None] | None = None


def get_cross_check_reader() -> LocalOcrReaderPort | None:
    """Return the configured cross-check reader, or None when disabled.

    Cached per provider value so the (expensive) engine init happens once.
    """
    global _reader_cache
    provider = (settings.ap_ocr_cross_check_provider or "").strip().lower()
    if _reader_cache is not None and _reader_cache[0] == provider:
        return _reader_cache[1]

    reader: LocalOcrReaderPort | None
    if provider in ("", "off", "none", "disabled"):
        reader = None
    elif provider == "paddle":
        candidate = PaddleOcrReader()
        reader = candidate if candidate.is_available() else None
    else:
        # Room for "cloud" and others later; unknown values disable the feature.
        logger.warning("[ocr_xcheck] Unknown AP_OCR_CROSS_CHECK_PROVIDER=%r; disabled.", provider)
        reader = None

    _reader_cache = (provider, reader)
    return reader


# ── Field comparison ──────────────────────────────────────────────────────────

_CCY_TOKENS: dict[str, tuple[str, ...]] = {
    "HKD": ("hkd", "hk$", "$"),
    "USD": ("usd", "us$", "$"),
    "CNY": ("cny", "rmb", "¥", "￥", "人民币"),
    "RMB": ("cny", "rmb", "¥", "￥", "人民币"),
    "EUR": ("eur", "€"),
    "GBP": ("gbp", "£"),
    "JPY": ("jpy", "yen", "¥", "￥", "円"),
    "SGD": ("sgd", "s$", "$"),
    "TWD": ("twd", "nt$", "ntd", "$"),
}

_CJK_RE = re.compile(r"[\u3400-\u9fff\u3040-\u30ff]")


def _amount_digits(value: object) -> str:
    """Significant digits of a money value, trailing decimal zeros trimmed.

    '326.70' -> '3267', '326.00' -> '326', '1,234.5' -> '12345'.
    """
    s = str(value or "").strip().replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", s)
    if not m:
        return ""
    num = m.group(0)
    if "." in num:
        num = num.rstrip("0").rstrip(".")
    return num.replace(".", "")


def _text_overlap_ratio(needle: str, haystack_lower: str) -> float:
    """Fraction of significant units in needle that appear in haystack.

    Significant units: ascii word tokens (len >= 2) and individual CJK chars,
    so the check works for both Latin and Chinese/Japanese merchant names.
    """
    units: list[str] = []
    for token in re.split(r"[^0-9A-Za-z]+", needle.lower()):
        if len(token) >= 2:
            units.append(token)
    units.extend(ch for ch in needle if _CJK_RE.match(ch))
    if not units:
        return 1.0  # nothing comparable; do not flag
    present = sum(1 for u in units if u in haystack_lower)
    return present / len(units)


def cross_check_fields(ocr_text: str, row: Mapping[str, object]) -> ValidationResult:
    """Compare a VLM row against an independent OCR reading.

    Only non-empty row fields are checked (empty fields are covered by other
    validators). Returns flags ocr_xcheck_{amount,currency,date,merchant}_mismatch
    and needs_review when any fire. A blank ocr_text yields no flags.
    """
    text = (ocr_text or "").strip()
    if not text:
        return ValidationResult(needs_review=False, validation_flags=())

    text_lower = text.lower()
    text_digits = re.sub(r"[^0-9]", "", text)
    flags: list[str] = []

    amount_digits = _amount_digits(row.get("amount"))
    if amount_digits and amount_digits not in text_digits:
        flags.append("ocr_xcheck_amount_mismatch")

    currency = str(row.get("currency") or "").strip().upper()
    if currency:
        tokens = _CCY_TOKENS.get(currency, (currency.lower(),))
        if not any(tok in text_lower for tok in tokens):
            flags.append("ocr_xcheck_currency_mismatch")

    date_s = str(row.get("date") or "").strip()
    if date_s:
        parts = [p for p in re.split(r"[^0-9]", date_s) if p]
        if parts and not all(p in text_digits for p in parts):
            flags.append("ocr_xcheck_date_mismatch")

    merchant = str(row.get("payee") or "").strip()
    if merchant:
        threshold = settings.ap_ocr_cross_check_merchant_min_overlap
        if _text_overlap_ratio(merchant, text_lower) < threshold:
            flags.append("ocr_xcheck_merchant_mismatch")

    return ValidationResult(needs_review=bool(flags), validation_flags=tuple(flags))

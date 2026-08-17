"""
Optional indicative HKD amounts using Open Exchange Rates (USD cross). Display only; not ledger or tax advice.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any

logger = logging.getLogger(__name__)

_CACHE: dict[str, Any] = {"ts": 0.0, "rates": None}


def _fetch_usd_rates() -> dict[str, float] | None:
    app_id = os.getenv("OPENEXCHANGERATES_APP_ID", "").strip()
    if not app_id:
        return None
    now = time.monotonic()
    if _CACHE["rates"] is not None and now - float(_CACHE["ts"]) < 3600:
        return _CACHE["rates"]  # type: ignore[return-value]
    url = f"https://openexchangerates.org/api/latest.json?app_id={app_id}"
    try:
        with urllib.request.urlopen(url, timeout=12) as resp:
            data = json.loads(resp.read().decode())
        rates = data.get("rates") or {}
        out = {str(k).upper(): float(v) for k, v in rates.items() if isinstance(v, (int, float))}
        _CACHE["ts"] = now
        _CACHE["rates"] = out
        return out
    except (urllib.error.URLError, ValueError, TypeError, OSError) as e:
        logger.warning("FX reference fetch failed: %s", e)
        return None


def hkd_reference_fields(amount: Decimal | str | None, currency: str | None) -> dict[str, Any] | None:
    """Optional fx_rate and ref_hkd_amount for UI; None if no API key or conversion unavailable."""
    if amount is None:
        return None
    try:
        amt = amount if isinstance(amount, Decimal) else Decimal(str(amount).replace(",", ""))
    except Exception:
        return None
    cur = (currency or "HKD").strip().upper()
    if cur == "HKD":
        return {
            "fx_rate": 1.0,
            "ref_hkd_amount": float(amt),
            "fx_disclaimer": "Indicative HKD equivalent for display only; not tax or accounting advice.",
        }
    rates = _fetch_usd_rates()
    if not rates or "HKD" not in rates:
        return None
    cur_rate = rates.get(cur)
    hkd_per_usd = rates.get("HKD")
    if not cur_rate or not hkd_per_usd:
        return None
    try:
        usd_amt = float(amt) / cur_rate
        hkd = usd_amt * hkd_per_usd
        div = float(amt)
        fx_rate = round(hkd / div, 6) if div else None
        return {
            "fx_rate": fx_rate,
            "ref_hkd_amount": round(hkd, 2),
            "fx_disclaimer": "Indicative HKD equivalent via OpenExchangeRates (USD cross); display only.",
        }
    except Exception:
        return None

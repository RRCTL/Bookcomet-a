"""Receipt vs company FX helpers. Empty receipt currency stays empty (do not invent HKD)."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

HKD_ALIASES = frozenset(
    {
        "HKD",
        "HK$",
        "HK",
        "HONG KONG DOLLAR",
        "HONGKONG DOLLAR",
        "HONG KONG DOLLARS",
        "港元",
        "港幣",
        "港币",
    }
)

CODE_ALIASES = {
    "JYP": "JPY",
    "YEN": "JPY",
    "RMB": "CNY",
    "CNH": "CNY",
    "人民幣": "CNY",
    "人民币": "CNY",
    "日元": "JPY",
    "US$": "USD",
    "USD$": "USD",
}

RATE_Q = Decimal("0.0001")
MONEY_Q = Decimal("0.01")


def normalize_currency_code(raw: str | None) -> str:
    c = (raw or "").strip()
    if not c:
        return ""
    if c in HKD_ALIASES or c.upper() in HKD_ALIASES:
        return "HKD"
    upper = c.upper()
    return CODE_ALIASES.get(upper) or CODE_ALIASES.get(c) or upper


def parse_fx_number(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    try:
        return Decimal(str(raw).replace(",", ""))
    except Exception:
        return None


def quantize_rate(rate: Decimal | float | int) -> Decimal:
    return Decimal(str(rate)).quantize(RATE_Q, rounding=ROUND_HALF_UP)


def quantize_money(amount: Decimal | float | int) -> Decimal:
    return Decimal(str(amount)).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def convert_receipt_to_company(receipt_amount: Decimal | float | int, rate: Decimal | float | int) -> Decimal:
    return quantize_money(Decimal(str(receipt_amount)) * quantize_rate(rate))


def implied_rate_from_printed(receipt_amount: Decimal | float | int, company_amount: Decimal | float | int) -> Decimal | None:
    rec = Decimal(str(receipt_amount))
    if rec == 0:
        return None
    return quantize_rate(Decimal(str(company_amount)) / rec)


def fx_pair_key(from_ccy: str, to_ccy: str) -> str:
    return f"{normalize_currency_code(from_ccy)}:{normalize_currency_code(to_ccy)}"


def same_currency(receipt: str | None, company: str | None) -> bool:
    a = normalize_currency_code(receipt)
    b = normalize_currency_code(company)
    return bool(a and b and a == b)


def row_ready_for_books(row: dict[str, Any], company_currency: str | None = None) -> bool:
    receipt_amt = parse_fx_number(row.get("amount"))
    if receipt_amt is None:
        return True
    receipt = normalize_currency_code(row.get("currency"))
    company = normalize_currency_code(row.get("company_currency") or company_currency)
    if same_currency(receipt, company):
        return True
    company_amt = parse_fx_number(row.get("company_amount"))
    return bool(company and company_amt is not None)


def reporting_currency_from_settings(custom: dict[str, Any] | None) -> str:
    if not isinstance(custom, dict):
        return ""
    raw = custom.get("reporting_currency")
    if raw in (None, ""):
        raw = custom.get("currency")
    return normalize_currency_code(raw) if isinstance(raw, str) else ""


def fx_rates_from_settings(custom: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(custom, dict):
        return {}
    raw = custom.get("fx_rates")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, value in raw.items():
        n = parse_fx_number(value)
        if n is not None and n > 0:
            out[str(key)] = float(quantize_rate(n))
    return out


def lookup_fx_rate(fx_rates: dict[str, Any] | None, from_ccy: str, to_ccy: str) -> Decimal | None:
    if not isinstance(fx_rates, dict):
        return None
    raw = fx_rates.get(fx_pair_key(from_ccy, to_ccy))
    n = parse_fx_number(raw)
    if n is None or n <= 0:
        return None
    return quantize_rate(n)


def merge_fx_settings(
    existing: dict[str, Any] | None,
    incoming: dict[str, Any] | None,
) -> dict[str, Any]:
    """Shallow-merge custom_settings, deep-merge fx_rates, validate reporting_currency."""
    base = dict(existing) if isinstance(existing, dict) else {}
    inc = dict(incoming) if isinstance(incoming, dict) else {}
    merged_rates = dict(fx_rates_from_settings(base))
    if "fx_rates" in inc:
        incoming_rates = inc.get("fx_rates")
        if incoming_rates is None:
            merged_rates = {}
        elif isinstance(incoming_rates, dict):
            merged_rates.update(fx_rates_from_settings({"fx_rates": incoming_rates}))
    merged = {**base, **inc}
    if "reporting_currency" in inc:
        raw = inc.get("reporting_currency")
        merged["reporting_currency"] = normalize_currency_code(raw) if isinstance(raw, str) and raw.strip() else None
    if "fx_rates" in inc or merged_rates:
        merged["fx_rates"] = merged_rates
    return merged


def company_amount_for_match(row: Any) -> tuple[str, float | None]:
    company_ccy = normalize_currency_code(getattr(row, "company_currency", None) or (row.get("company_currency") if isinstance(row, dict) else None))
    company_amt = parse_fx_number(getattr(row, "company_amount", None) if not isinstance(row, dict) else row.get("company_amount"))
    if company_ccy and company_amt is not None:
        return company_ccy, float(quantize_money(company_amt))
    receipt_ccy = normalize_currency_code(getattr(row, "currency", None) or (row.get("currency") if isinstance(row, dict) else None))
    receipt_amt = parse_fx_number(getattr(row, "amount", None) if not isinstance(row, dict) else row.get("amount"))
    return receipt_ccy, float(quantize_money(receipt_amt)) if receipt_amt is not None else None


def apply_saved_rate_to_row(
    row: dict[str, Any],
    company_currency: str,
    rate: Decimal | float | None,
    *,
    keep_printed: bool = True,
) -> dict[str, Any]:
    out = dict(row)
    receipt = normalize_currency_code(row.get("currency"))
    company = normalize_currency_code(company_currency)
    receipt_amt = parse_fx_number(row.get("amount"))
    tax_amt = parse_fx_number(row.get("tax_amount"))
    if not company:
        return out
    if same_currency(receipt, company):
        out["company_currency"] = company
        out["company_amount"] = float(quantize_money(receipt_amt)) if receipt_amt is not None else None
        out["company_tax_amount"] = float(quantize_money(tax_amt)) if tax_amt is not None else None
        out["exchange_rate"] = 1.0
        return out
    printed = parse_fx_number(row.get("printed_company_amount"))
    if keep_printed and printed is not None and receipt_amt is not None:
        implied = implied_rate_from_printed(receipt_amt, printed)
        out["company_currency"] = company
        out["company_amount"] = float(quantize_money(printed))
        out["exchange_rate"] = float(implied) if implied is not None else None
        if tax_amt is not None and implied is not None:
            out["company_tax_amount"] = float(convert_receipt_to_company(tax_amt, implied))
        return out
    if rate is None or receipt_amt is None:
        out["company_currency"] = company
        return out
    q_rate = quantize_rate(rate)
    out["company_currency"] = company
    out["company_amount"] = float(convert_receipt_to_company(receipt_amt, q_rate))
    out["company_tax_amount"] = float(convert_receipt_to_company(tax_amt, q_rate)) if tax_amt is not None else None
    out["exchange_rate"] = float(q_rate)
    return out

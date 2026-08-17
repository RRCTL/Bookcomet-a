"""
Depreciation Engine — computes per-period depreciation schedules.

Supported methods:
  straight_line     — equal amounts every period until fully depreciated
  declining_balance — percentage of net book value each period

Both methods stop depreciating once NBV reaches the residual value.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any


def _add_months(dt: datetime, months: int) -> datetime:
    """Add N months to a datetime (clamped to month-end if needed)."""
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, _days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (_add_months(datetime(year, month, 1), 1) - timedelta(days=1)).day


def compute_straight_line(
    purchase_amount: float,
    residual_value: float,
    useful_life_months: int,
    acquisition_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Straight-line depreciation schedule.

    Returns a list of period dicts:
      period_number, period_start, period_end, depreciation_amount,
      accumulated_at_period_end, net_book_value_at_period_end
    """
    if useful_life_months <= 0 or purchase_amount <= residual_value:
        return []

    depreciable = purchase_amount - residual_value
    monthly_dep = depreciable / useful_life_months

    start = acquisition_date or datetime.now()
    accumulated = 0.0
    schedule = []

    for i in range(1, useful_life_months + 1):
        period_start = _add_months(start, i - 1)
        period_end = _add_months(start, i) - timedelta(days=1)

        # Last period: use remainder to avoid floating-point drift
        if i == useful_life_months:
            dep = depreciable - accumulated
        else:
            dep = round(monthly_dep, 2)

        dep = max(0.0, min(dep, purchase_amount - residual_value - accumulated))
        accumulated = round(accumulated + dep, 2)
        nbv = round(purchase_amount - accumulated, 2)

        schedule.append({
            "period_number": i,
            "period_start": period_start,
            "period_end": period_end,
            "period_type": "monthly",
            "depreciation_amount": dep,
            "accumulated_at_period_end": accumulated,
            "net_book_value_at_period_end": nbv,
        })

        if accumulated >= depreciable:
            break

    return schedule


def compute_declining_balance(
    purchase_amount: float,
    residual_value: float,
    useful_life_months: int,
    annual_rate_pct: float | None = None,
    acquisition_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Declining-balance depreciation schedule.

    If annual_rate_pct is None, the double-declining rate is derived from
    useful_life_months: rate = 2 / (useful_life_months / 12).

    Returns same structure as compute_straight_line.
    """
    if useful_life_months <= 0 or purchase_amount <= residual_value:
        return []

    useful_life_years = useful_life_months / 12
    if annual_rate_pct is not None:
        annual_rate = annual_rate_pct / 100
    else:
        annual_rate = 2 / useful_life_years  # double-declining default

    monthly_rate = 1 - (1 - annual_rate) ** (1 / 12)

    start = acquisition_date or datetime.now()
    nbv = purchase_amount
    accumulated = 0.0
    schedule = []

    for i in range(1, useful_life_months + 1):
        period_start = _add_months(start, i - 1)
        period_end = _add_months(start, i) - timedelta(days=1)

        dep = round(nbv * monthly_rate, 2)

        # Clamp so NBV doesn't fall below residual_value
        max_dep = max(0.0, nbv - residual_value)
        dep = min(dep, max_dep)

        nbv = round(nbv - dep, 2)
        accumulated = round(accumulated + dep, 2)

        schedule.append({
            "period_number": i,
            "period_start": period_start,
            "period_end": period_end,
            "period_type": "monthly",
            "depreciation_amount": dep,
            "accumulated_at_period_end": accumulated,
            "net_book_value_at_period_end": nbv,
        })

        if nbv <= residual_value or dep == 0:
            break

    return schedule


def compute_schedule(
    purchase_amount: float,
    residual_value: float,
    useful_life_months: int,
    method: str = "straight_line",
    annual_rate_pct: float | None = None,
    acquisition_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Unified entry-point.  method: 'straight_line' | 'declining_balance'
    """
    if method == "declining_balance":
        return compute_declining_balance(
            purchase_amount=purchase_amount,
            residual_value=residual_value,
            useful_life_months=useful_life_months,
            annual_rate_pct=annual_rate_pct,
            acquisition_date=acquisition_date,
        )
    return compute_straight_line(
        purchase_amount=purchase_amount,
        residual_value=residual_value,
        useful_life_months=useful_life_months,
        acquisition_date=acquisition_date,
    )


def compute_loan_schedule(
    principal: float,
    annual_interest_rate_pct: float,
    tenor_months: int,
    start_date: datetime | None = None,
) -> list[dict[str, Any]]:
    """
    Standard amortising loan schedule (PMT formula).

    Returns list of installment dicts:
      installment_number, due_date, principal_portion, interest_portion,
      total_payment, outstanding_principal_after
    """
    if tenor_months <= 0 or principal <= 0:
        return []

    start = start_date or datetime.now()
    monthly_rate = (annual_interest_rate_pct / 100) / 12

    if monthly_rate == 0:
        pmt = round(principal / tenor_months, 2)
    else:
        pmt = principal * monthly_rate / (1 - (1 + monthly_rate) ** -tenor_months)
        pmt = round(pmt, 2)

    outstanding = principal
    schedule = []

    for i in range(1, tenor_months + 1):
        due = _add_months(start, i)
        interest = round(outstanding * monthly_rate, 2)
        principal_portion = round(pmt - interest, 2)

        # Last installment: clear remaining principal
        if i == tenor_months:
            principal_portion = round(outstanding, 2)
            pmt = round(principal_portion + interest, 2)

        outstanding = round(outstanding - principal_portion, 2)
        if outstanding < 0:
            outstanding = 0.0

        schedule.append({
            "installment_number": i,
            "due_date": due,
            "principal_portion": principal_portion,
            "interest_portion": interest,
            "total_payment": pmt,
            "outstanding_principal_after": outstanding,
        })

    return schedule

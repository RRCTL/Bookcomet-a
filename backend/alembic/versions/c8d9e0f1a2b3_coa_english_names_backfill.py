"""backfill English Chart of Accounts names (English-only UI)

Revision ID: c8d9e0f1a2b3
Revises: b7c8d9e0f1a2
Create Date: 2026-06-16

Notes
-----
Chart of Accounts rows are referenced everywhere by ``code`` (e.g. 1010), not by
name, and each row already carries a first-class English name in ``name_en``.
This migration is therefore non-destructive: it only ensures every built-in
default account has its canonical English ``name_en`` populated (defensive
backfill for any drift). It intentionally leaves ``name_zh`` intact because the
AI prompt builder (get_prompt_account_lines) still consumes it; the English-only
requirement is satisfied at the display layer by rendering ``name_en``.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Canonical English names keyed by account code (mirror of chart_of_accounts.py).
_EN_BY_CODE: dict[str, str] = {
    "1010": "Cash at Bank", "1020": "Petty Cash", "1100": "Accounts Receivable",
    "1110": "Trade Debtors", "1200": "Inventory", "1300": "Prepaid Expenses",
    "1400": "Property, Plant & Equipment", "1500": "Other Current Assets",
    "2100": "Accounts Payable", "2200": "Accrued Liabilities", "2300": "Tax Payable",
    "2400": "Bank Loan", "2500": "Other Current Liabilities",
    "3100": "Share Capital", "3200": "Retained Earnings", "3300": "Owner's Drawing",
    "4010": "Sales", "4020": "Service Income", "4030": "Interest Received",
    "4040": "Rent Income", "4050": "Other Income",
    "5010": "Rent", "5020": "Utilities", "5030": "Office Supplies",
    "5040": "Professional Fees", "5050": "Insurance", "5060": "Travel & Entertainment",
    "5070": "Advertising & Marketing", "5080": "Bank Fee", "5090": "Interest Paid",
    "5100": "Purchases / COGS", "5110": "Other Expense",
}


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    return table_name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if not _has_table("chart_of_accounts"):
        return
    bind = op.get_bind()
    coa = sa.table(
        "chart_of_accounts",
        sa.column("code", sa.String),
        sa.column("name_en", sa.String),
    )
    for code, name_en in _EN_BY_CODE.items():
        bind.execute(
            coa.update()
            .where(coa.c.code == code)
            .where(sa.or_(coa.c.name_en.is_(None), coa.c.name_en == ""))
            .values(name_en=name_en)
        )


def downgrade() -> None:
    # Non-destructive backfill; nothing to revert.
    pass

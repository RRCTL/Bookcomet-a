"""add company currency fields on bank and ledger transactions

Revision ID: c4d5e6f7a8b9
Revises: a0b1c2d3e4f5
Create Date: 2026-09-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "a0b1c2d3e4f5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "bank_transactions", "company_currency"):
        op.add_column("bank_transactions", sa.Column("company_currency", sa.String(), nullable=True))
    if not _has_column(bind, "bank_transactions", "company_amount"):
        op.add_column("bank_transactions", sa.Column("company_amount", sa.Float(), nullable=True))
    if not _has_column(bind, "bank_transactions", "exchange_rate"):
        op.add_column("bank_transactions", sa.Column("exchange_rate", sa.Float(), nullable=True))
    if not _has_column(bind, "ledger_transactions", "company_currency"):
        op.add_column("ledger_transactions", sa.Column("company_currency", sa.String(), nullable=True))
    if not _has_column(bind, "ledger_transactions", "company_amount"):
        op.add_column("ledger_transactions", sa.Column("company_amount", sa.Float(), nullable=True))
    if not _has_column(bind, "ledger_transactions", "company_tax_amount"):
        op.add_column("ledger_transactions", sa.Column("company_tax_amount", sa.Float(), nullable=True))
    if not _has_column(bind, "ledger_transactions", "exchange_rate"):
        op.add_column("ledger_transactions", sa.Column("exchange_rate", sa.Float(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    for table, cols in (
        ("bank_transactions", ("exchange_rate", "company_amount", "company_currency")),
        ("ledger_transactions", ("exchange_rate", "company_tax_amount", "company_amount", "company_currency")),
    ):
        for col in cols:
            if _has_column(bind, table, col):
                op.drop_column(table, col)

"""add dr_cr column to ledger_transactions

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-07-24

Stores explicit Dr/Cr side from AR/AP module rows for RECON display and GL drafts.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, None] = "e0f1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "ledger_transactions" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("ledger_transactions")}
    if "dr_cr" not in cols:
        op.add_column("ledger_transactions", sa.Column("dr_cr", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "ledger_transactions" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("ledger_transactions")}
    if "dr_cr" in cols:
        op.drop_column("ledger_transactions", "dr_cr")

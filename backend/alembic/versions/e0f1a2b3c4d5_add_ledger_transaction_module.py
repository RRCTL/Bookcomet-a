"""add module column to ledger_transactions

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-06-18

Tags the source module (AP / AR) on ledger rows imported when a workflow run is
approved, so the next-phase reconciliation / reports can split AP vs AR.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e0f1a2b3c4d5"
down_revision: Union[str, None] = "d9e0f1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ledger_transactions", sa.Column("module", sa.String(), nullable=True))
    op.create_index("ix_ledger_transactions_module", "ledger_transactions", ["module"])


def downgrade() -> None:
    op.drop_index("ix_ledger_transactions_module", table_name="ledger_transactions")
    op.drop_column("ledger_transactions", "module")

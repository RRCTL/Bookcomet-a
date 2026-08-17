"""Add account_category to transaction tables

Revision ID: 8a9c3f0de2a1
Revises: b2b9e0e2d060
Create Date: 2026-02-12 10:30:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "8a9c3f0de2a1"
down_revision: Union[str, None] = "b2b9e0e2d060"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("ledger_transactions", sa.Column("account_category", sa.String(), nullable=True))
    op.add_column("bank_transactions", sa.Column("account_category", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("bank_transactions", "account_category")
    op.drop_column("ledger_transactions", "account_category")

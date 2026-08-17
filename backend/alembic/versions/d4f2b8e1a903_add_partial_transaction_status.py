"""add_partial_transaction_status

Revision ID: d4f2b8e1a903
Revises: c3e1a9d7f201
Create Date: 2026-03-08 01:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4f2b8e1a903'
down_revision: Union[str, None] = 'c3e1a9d7f201'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite stores enums as VARCHAR — the new value is automatically accepted.
    # For PostgreSQL uncomment the lines below:
    # op.execute("ALTER TYPE transactionstatus ADD VALUE IF NOT EXISTS 'partial'")
    pass


def downgrade() -> None:
    # SQLite: no action needed.
    # For PostgreSQL: enum values cannot be removed without recreating the type.
    pass

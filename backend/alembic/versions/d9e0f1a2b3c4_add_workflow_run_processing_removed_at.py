"""add processing_removed_at column to workflow_runs

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-06-17

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def upgrade() -> None:
    if not _has_column("workflow_runs", "processing_removed_at"):
        op.add_column("workflow_runs", sa.Column("processing_removed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    if _has_column("workflow_runs", "processing_removed_at"):
        op.drop_column("workflow_runs", "processing_removed_at")

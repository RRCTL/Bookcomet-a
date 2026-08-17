"""workflow run file batch table preset

Revision ID: z5a6b7c8d9e0
Revises: y4z5a6b7c8d9
Create Date: 2026-05-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z5a6b7c8d9e0"
down_revision: Union[str, None] = "y4z5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workflow_run_files", sa.Column("batch_table_preset", sa.String(), nullable=True))
    op.add_column("workflow_run_files", sa.Column("batch_receipt_signal", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("workflow_run_files", "batch_receipt_signal")
    op.drop_column("workflow_run_files", "batch_table_preset")

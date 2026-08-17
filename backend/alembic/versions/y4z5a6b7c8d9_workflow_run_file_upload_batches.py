"""workflow run file upload batches

Revision ID: y4z5a6b7c8d9
Revises: x3n4o5d6e7f8
Create Date: 2026-05-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y4z5a6b7c8d9"
down_revision: Union[str, None] = "x3n4o5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("workflow_run_files", sa.Column("upload_batch_id", sa.String(), nullable=True))
    op.add_column("workflow_run_files", sa.Column("uploaded_at", sa.DateTime(), nullable=True))
    op.add_column("workflow_run_files", sa.Column("batch_committed_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_workflow_run_files_upload_batch_id",
        "workflow_run_files",
        ["upload_batch_id"],
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE workflow_run_files
            SET upload_batch_id = run_id || '-legacy',
                uploaded_at = COALESCE(created_at, CURRENT_TIMESTAMP),
                batch_committed_at = CASE
                    WHEN file_status != 'pending' THEN COALESCE(created_at, CURRENT_TIMESTAMP)
                    ELSE NULL
                END
            WHERE upload_batch_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index("ix_workflow_run_files_upload_batch_id", table_name="workflow_run_files")
    op.drop_column("workflow_run_files", "batch_committed_at")
    op.drop_column("workflow_run_files", "uploaded_at")
    op.drop_column("workflow_run_files", "upload_batch_id")

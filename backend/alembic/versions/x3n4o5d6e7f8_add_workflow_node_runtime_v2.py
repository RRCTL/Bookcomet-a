"""add workflow node executions and pool2 packages

Revision ID: x3n4o5d6e7f8
Revises: w2f3o4l5d6e7
Create Date: 2026-05-26

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x3n4o5d6e7f8"
down_revision: Union[str, None] = "w2f3o4l5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_node_executions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("company_id", sa.String(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("node_type", sa.String(40), nullable=False),
        sa.Column("item_key", sa.String(), nullable=True),
        sa.Column("cache_key", sa.String(64), nullable=True),
        sa.Column("input_hash", sa.String(64), nullable=True),
        sa.Column("content_id", sa.String(64), nullable=True),
        sa.Column("storage_path", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(40), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("token_usage_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_node_executions_run_id", "workflow_node_executions", ["run_id"])
    op.create_index("ix_workflow_node_executions_cache_key", "workflow_node_executions", ["cache_key"])

    op.create_table(
        "workflow_pool2_packages",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("company_id", sa.String(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("task_id", sa.String(), sa.ForeignKey("chat_tasks.id"), nullable=False),
        sa.Column("processing_mode", sa.String(20), nullable=False),
        sa.Column("package_id", sa.String(64), nullable=False),
        sa.Column("storage_path", sa.Text(), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=True),
        sa.Column("snapshot_message_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_pool2_packages_run_id", "workflow_pool2_packages", ["run_id"])


def downgrade() -> None:
    op.drop_table("workflow_pool2_packages")
    op.drop_table("workflow_node_executions")

"""add workflow_runs, workflow_templates, workflow_run_files

Revision ID: v1w2o3r4k5f6
Revises: u3b4c5d6e7f8
Create Date: 2026-05-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v1w2o3r4k5f6"
down_revision: Union[str, None] = "u3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("task_id", sa.String(), sa.ForeignKey("chat_tasks.id"), nullable=False),
        sa.Column("owner_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("processing_mode", sa.String(20), nullable=False),
        sa.Column("graph_json", sa.JSON(), nullable=False),
        sa.Column("run_status", sa.String(30), nullable=False, server_default="draft"),
        sa.Column("node_states_json", sa.JSON(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("title_generated", sa.Boolean(), server_default=sa.text("0")),
        sa.Column("snapshot_message_id", sa.String(), nullable=True),
        sa.Column("console_log_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index("ix_workflow_runs_company_id", "workflow_runs", ["company_id"])

    op.create_table(
        "workflow_templates",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("processing_mode", sa.String(20), nullable=False),
        sa.Column("graph_json", sa.JSON(), nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("0")),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_templates_company_id", "workflow_templates", ["company_id"])

    op.create_table(
        "workflow_run_files",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), sa.ForeignKey("workflow_runs.id"), nullable=False),
        sa.Column("task_file_id", sa.String(), sa.ForeignKey("task_files.id"), nullable=False),
        sa.Column("vlm_job_id", sa.String(), sa.ForeignKey("background_jobs.id"), nullable=True),
        sa.Column("file_status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("gate_result", sa.String(40), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("result_summary_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_run_files_run_id", "workflow_run_files", ["run_id"])


def downgrade() -> None:
    op.drop_table("workflow_run_files")
    op.drop_table("workflow_templates")
    op.drop_table("workflow_runs")

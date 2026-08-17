"""add workflow skills

Revision ID: a6b7c8d9e0f1
Revises: z5a6b7c8d9e0
Create Date: 2026-06-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, None] = "z5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "workflow_skills",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("skill_key", sa.String(120), nullable=False),
        sa.Column("structured_json", sa.JSON(), nullable=False),
        sa.Column("generated_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "mode", "skill_key", name="uq_workflow_skill_company_mode_key"),
    )
    op.create_index("ix_workflow_skills_company_id", "workflow_skills", ["company_id"])

    op.create_table(
        "workflow_skill_versions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("skill_id", sa.String(), sa.ForeignKey("workflow_skills.id"), nullable=False),
        sa.Column("company_id", sa.String(), sa.ForeignKey("companies.id"), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("skill_key", sa.String(120), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("structured_json", sa.JSON(), nullable=False),
        sa.Column("generated_markdown", sa.Text(), nullable=False, server_default=""),
        sa.Column("saved_by_user_id", sa.String(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("saved_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_workflow_skill_versions_skill_id", "workflow_skill_versions", ["skill_id"])
    op.create_index("ix_workflow_skill_versions_company_id", "workflow_skill_versions", ["company_id"])


def downgrade() -> None:
    op.drop_table("workflow_skill_versions")
    op.drop_table("workflow_skills")

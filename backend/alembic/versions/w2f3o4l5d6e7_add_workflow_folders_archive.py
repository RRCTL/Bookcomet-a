"""add workflow folders and run archive fields

Revision ID: w2f3o4l5d6e7
Revises: v1w2o3r4k5f6
Create Date: 2026-05-20

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w2f3o4l5d6e7"
down_revision: Union[str, None] = "v1w2o3r4k5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    return table_name in sa.inspect(bind).get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(i["name"] == index_name for i in insp.get_indexes(table_name))


def upgrade() -> None:
    # Table may exist via runtime create_all before Alembic reaches this revision.
    if not _has_table("workflow_folders"):
        op.create_table(
            "workflow_folders",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("parent_id", sa.String(), sa.ForeignKey("workflow_folders.id"), nullable=True),
            sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
    if not _has_index("workflow_folders", "ix_workflow_folders_company_id"):
        op.create_index("ix_workflow_folders_company_id", "workflow_folders", ["company_id"])

    if not _has_column("workflow_runs", "folder_id"):
        op.add_column("workflow_runs", sa.Column("folder_id", sa.String(), nullable=True))
    if not _has_column("workflow_runs", "archived_at"):
        op.add_column("workflow_runs", sa.Column("archived_at", sa.DateTime(), nullable=True))

    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_workflow_runs_folder_id",
            "workflow_runs",
            "workflow_folders",
            ["folder_id"],
            ["id"],
        )
    if not _has_index("workflow_runs", "ix_workflow_runs_folder_id"):
        op.create_index("ix_workflow_runs_folder_id", "workflow_runs", ["folder_id"])


def downgrade() -> None:
    if _has_index("workflow_runs", "ix_workflow_runs_folder_id"):
        op.drop_index("ix_workflow_runs_folder_id", table_name="workflow_runs")
    bind = op.get_bind()
    if bind.dialect.name != "sqlite" and _has_column("workflow_runs", "folder_id"):
        op.drop_constraint("fk_workflow_runs_folder_id", "workflow_runs", type_="foreignkey")
    if _has_column("workflow_runs", "archived_at"):
        op.drop_column("workflow_runs", "archived_at")
    if _has_column("workflow_runs", "folder_id"):
        op.drop_column("workflow_runs", "folder_id")
    if _has_table("workflow_folders"):
        op.drop_table("workflow_folders")

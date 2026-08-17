"""add company_profiles table

Company profile / wizard fields (industry, accounting basis, profile_md, etc.).
Previously only created via Base.metadata.create_all when DB_AUTO_CREATE_ON_STARTUP
ran in local mode; Alembic-only databases were missing this table.

Revision ID: r8a9b0c1d2e4
Revises: q2b3c4d5e6f7
Create Date: 2026-04-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r8a9b0c1d2e4"
down_revision: Union[str, None] = "q2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    insp = sa.inspect(conn)
    return table_name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "company_profiles"):
        return

    op.create_table(
        "company_profiles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("accounting_basis", sa.String(), nullable=True),
        sa.Column("fiscal_year_end", sa.String(), nullable=True),
        sa.Column("company_name", sa.String(), nullable=True),
        sa.Column("company_name_keywords", sa.JSON(), nullable=True),
        sa.Column("custom_settings", sa.JSON(), nullable=True),
        sa.Column("profile_md", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", name="uq_company_profiles_company_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "company_profiles"):
        return
    op.drop_table("company_profiles")

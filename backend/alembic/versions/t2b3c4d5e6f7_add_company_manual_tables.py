"""add company_manuals and company_manual_versions tables

Versioned company accounting manual (Markdown) for AI context.
Previously only existed when Base.metadata.create_all ran with DB_AUTO_CREATE_ON_STARTUP.

Revision ID: t2b3c4d5e6f7
Revises: s1a2b3c4d5e6
Create Date: 2026-04-29

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t2b3c4d5e6f7"
down_revision: Union[str, None] = "s1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    insp = sa.inspect(conn)
    return table_name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "company_manuals"):
        op.create_table(
            "company_manuals",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column("updated_by_user", sa.String(), nullable=True),
            sa.Column(
                "updated_by_type",
                sa.String(),
                nullable=False,
                server_default="user",
            ),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("company_id", name="uq_company_manuals_company_id"),
        )

    if not _table_exists(bind, "company_manual_versions"):
        op.create_table(
            "company_manual_versions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("manual_id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column(
                "saved_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column("saved_by", sa.String(), nullable=True),
            sa.Column(
                "saved_by_type",
                sa.String(),
                nullable=False,
                server_default="user",
            ),
            sa.ForeignKeyConstraint(["manual_id"], ["company_manuals.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_company_manual_versions_manual_id",
            "company_manual_versions",
            ["manual_id"],
            unique=False,
        )
        op.create_index(
            "ix_company_manual_versions_company_id",
            "company_manual_versions",
            ["company_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "company_manual_versions"):
        op.drop_index("ix_company_manual_versions_company_id", table_name="company_manual_versions")
        op.drop_index("ix_company_manual_versions_manual_id", table_name="company_manual_versions")
        op.drop_table("company_manual_versions")
    if _table_exists(bind, "company_manuals"):
        op.drop_table("company_manuals")

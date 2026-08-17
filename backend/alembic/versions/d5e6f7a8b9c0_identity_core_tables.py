"""identity core tables (companies, users, memberships)

Needed for migrations that reference these tables and for data migrations
that query memberships.

Revision ID: d5e6f7a8b9c0
Revises: d4f2b8e1a903
Create Date: 2026-04-28

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "d4f2b8e1a903"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    from sqlalchemy import text

    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "companies"):
        op.create_table(
            "companies",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=False),
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
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_companies_name"), "companies", ["name"], unique=False)

    if not _table_exists(bind, "users"):
        op.create_table(
            "users",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("email", sa.String(), nullable=False),
            sa.Column("display_name", sa.String(), nullable=False),
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
            sa.Column("hashed_password", sa.String(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True),
            sa.Column("is_verified", sa.Boolean(), nullable=True),
            sa.Column("verification_token", sa.String(), nullable=True),
            sa.Column("verification_token_expiry", sa.DateTime(), nullable=True),
            sa.Column("reset_token", sa.String(), nullable=True),
            sa.Column("reset_token_expiry", sa.DateTime(), nullable=True),
            sa.Column("refresh_token_hash", sa.String(), nullable=True),
            sa.Column("refresh_token_expires_at", sa.DateTime(), nullable=True),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
            sa.Column("failed_login_count", sa.Integer(), nullable=True),
            sa.Column("locked_until", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
        op.create_index(op.f("ix_users_verification_token"), "users", ["verification_token"], unique=False)
        op.create_index(op.f("ix_users_reset_token"), "users", ["reset_token"], unique=False)
        op.create_index(op.f("ix_users_refresh_token_hash"), "users", ["refresh_token_hash"], unique=False)

    if not _table_exists(bind, "memberships"):
        op.create_table(
            "memberships",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False),
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
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("user_id", "company_id", name="uq_membership_user_company"),
        )
        op.create_index(op.f("ix_memberships_user_id"), "memberships", ["user_id"], unique=False)
        op.create_index(op.f("ix_memberships_company_id"), "memberships", ["company_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_memberships_company_id"), table_name="memberships")
    op.drop_index(op.f("ix_memberships_user_id"), table_name="memberships")
    op.drop_table("memberships")

    op.drop_index(op.f("ix_users_refresh_token_hash"), table_name="users")
    op.drop_index(op.f("ix_users_reset_token"), table_name="users")
    op.drop_index(op.f("ix_users_verification_token"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    op.drop_index(op.f("ix_companies_name"), table_name="companies")
    op.drop_table("companies")

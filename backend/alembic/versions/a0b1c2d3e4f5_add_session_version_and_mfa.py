"""add session_version and MFA columns on users (SEC-CODE-008/009)

Revision ID: a0b1c2d3e4f5
Revises: h3u4s5e6r7n8
Create Date: 2026-08-12 08:20:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a0b1c2d3e4f5"
down_revision: Union[str, None] = "h3u4s5e6r7n8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "users", "session_version"):
        op.add_column(
            "users",
            sa.Column("session_version", sa.Integer(), nullable=False, server_default="0"),
        )
    if not _has_column(bind, "users", "mfa_enabled"):
        op.add_column(
            "users",
            sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _has_column(bind, "users", "mfa_secret"):
        op.add_column("users", sa.Column("mfa_secret", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "users", "mfa_secret"):
        op.drop_column("users", "mfa_secret")
    if _has_column(bind, "users", "mfa_enabled"):
        op.drop_column("users", "mfa_enabled")
    if _has_column(bind, "users", "session_version"):
        op.drop_column("users", "session_version")

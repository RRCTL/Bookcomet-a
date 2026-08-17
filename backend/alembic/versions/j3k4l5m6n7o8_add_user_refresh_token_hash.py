"""add_user_refresh_token_hash

Revision ID: j3k4l5m6n7o8
Revises: i2b3c4d5e6f7
Create Date: 2026-04-08 00:00:00.000000

Separate stored refresh token digest from password-reset token (users.reset_token).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "j3k4l5m6n7o8"
down_revision: Union[str, None] = "i2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_refresh_hash(value: str) -> bool:
    if not value or len(value) != 64:
        return False
    return all(c in "0123456789abcdef" for c in value.lower())


def _has_column(conn, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(conn)
    if table_name not in insp.get_table_names():
        return False
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def _has_index(conn, index_name: str) -> bool:
    r = conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name=:n"), {"n": index_name})
    return r.fetchone() is not None


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, "users", "refresh_token_hash"):
        op.add_column("users", sa.Column("refresh_token_hash", sa.String(), nullable=True))
    if not _has_index(conn, "ix_users_refresh_token_hash"):
        op.create_index("ix_users_refresh_token_hash", "users", ["refresh_token_hash"], unique=False)

    rows = conn.execute(sa.text("SELECT id, reset_token FROM users WHERE reset_token IS NOT NULL")).fetchall()
    for row in rows:
        uid, rt = row[0], row[1]
        if rt and _is_refresh_hash(str(rt)):
            conn.execute(
                sa.text(
                    "UPDATE users SET refresh_token_hash = :h, reset_token = NULL, "
                    "reset_token_expiry = NULL WHERE id = :id"
                ),
                {"h": str(rt), "id": uid},
            )


def downgrade() -> None:
    conn = op.get_bind()
    if _has_index(conn, "ix_users_refresh_token_hash"):
        op.drop_index("ix_users_refresh_token_hash", table_name="users")
    if _has_column(conn, "users", "refresh_token_hash"):
        op.drop_column("users", "refresh_token_hash")

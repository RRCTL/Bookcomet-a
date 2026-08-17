"""add verification_token_expiry and refresh_token_expires_at

Revision ID: k5m6n7o8p9q0
Revises: j3k4l5m6n7o8
Create Date: 2026-04-08 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "k5m6n7o8p9q0"
down_revision: Union[str, None] = "j3k4l5m6n7o8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "users", "verification_token_expiry"):
        op.add_column("users", sa.Column("verification_token_expiry", sa.DateTime(), nullable=True))
    if not _has_column(bind, "users", "refresh_token_expires_at"):
        op.add_column("users", sa.Column("refresh_token_expires_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, "users", "refresh_token_expires_at"):
        op.drop_column("users", "refresh_token_expires_at")
    if _has_column(bind, "users", "verification_token_expiry"):
        op.drop_column("users", "verification_token_expiry")

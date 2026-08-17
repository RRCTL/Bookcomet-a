"""add auth_audit_logs

Revision ID: q2b3c4d5e6f7
Revises: p1a2b3c4d5e6
Create Date: 2026-04-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "q2b3c4d5e6f7"
down_revision: Union[str, None] = "p1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    insp = sa.inspect(conn)
    return table_name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "auth_audit_logs"):
        return

    op.create_table(
        "auth_audit_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
        sa.Column("detail", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=True,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_auth_audit_logs_user_id"), "auth_audit_logs", ["user_id"], unique=False)
    op.create_index(op.f("ix_auth_audit_logs_event_type"), "auth_audit_logs", ["event_type"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "auth_audit_logs"):
        return
    op.drop_index(op.f("ix_auth_audit_logs_event_type"), table_name="auth_audit_logs")
    op.drop_index(op.f("ix_auth_audit_logs_user_id"), table_name="auth_audit_logs")
    op.drop_table("auth_audit_logs")

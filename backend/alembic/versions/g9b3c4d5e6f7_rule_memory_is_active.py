"""rule_memory_is_active

Revision ID: g9b3c4d5e6f7
Revises: f8a1b2c3d4e5
Create Date: 2026-03-30

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g9b3c4d5e6f7"
down_revision: Union[str, None] = "f8a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # Table may exist only via runtime create_all; migration chain did not CREATE it earlier.
    if "company_rule_memories" not in insp.get_table_names():
        op.create_table(
            "company_rule_memories",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column("updated_by_user", sa.String(), nullable=True),
            sa.Column("updated_by_type", sa.String(), nullable=False, server_default=sa.text("'user'")),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("company_id", "mode", name="uq_rule_memory_company_mode"),
        )
        op.create_index(
            op.f("ix_company_rule_memories_company_id"),
            "company_rule_memories",
            ["company_id"],
            unique=False,
        )
        return

    # Partial runs (add_column committed before a failed alter_column on SQLite) leave
    # is_active present while revision is not stamped — skip add if already there.
    if not _has_column("company_rule_memories", "is_active"):
        op.add_column(
            "company_rule_memories",
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "company_rule_memories" not in insp.get_table_names():
        return
    if _has_column("company_rule_memories", "is_active"):
        op.drop_column("company_rule_memories", "is_active")

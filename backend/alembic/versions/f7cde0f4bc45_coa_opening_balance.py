"""coa_opening_balance

Revision ID: f7cde0f4bc45
Revises: 8a9c3f0de2a1
Create Date: 2026-02-27 03:31:25.516482

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f7cde0f4bc45'
down_revision: Union[str, None] = '8a9c3f0de2a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "chart_of_accounts" not in insp.get_table_names():
        op.create_table(
            "chart_of_accounts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("code", sa.String(), nullable=False),
            sa.Column("name_en", sa.String(), nullable=False),
            sa.Column("name_zh", sa.String(), nullable=True),
            sa.Column("category_type", sa.String(), nullable=False),
            sa.Column("allowed_modes", sa.JSON(), nullable=False),
            sa.Column("is_default", sa.Boolean(), nullable=True),
            sa.Column("opening_balance", sa.Float(), nullable=True),
            sa.Column("opening_balance_dr_cr", sa.String(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_chart_of_accounts_company_id"),
            "chart_of_accounts",
            ["company_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_chart_of_accounts_code"),
            "chart_of_accounts",
            ["code"],
            unique=False,
        )
        return
    cols = {c["name"] for c in insp.get_columns("chart_of_accounts")}
    if "opening_balance" not in cols:
        op.add_column("chart_of_accounts", sa.Column("opening_balance", sa.Float(), nullable=True))
    if "opening_balance_dr_cr" not in cols:
        op.add_column("chart_of_accounts", sa.Column("opening_balance_dr_cr", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "chart_of_accounts" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("chart_of_accounts")}
    if "opening_balance_dr_cr" in cols:
        op.drop_column("chart_of_accounts", "opening_balance_dr_cr")
    if "opening_balance" in cols:
        op.drop_column("chart_of_accounts", "opening_balance")

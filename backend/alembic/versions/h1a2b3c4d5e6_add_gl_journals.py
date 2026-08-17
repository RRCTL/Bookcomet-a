"""add_gl_journals

Revision ID: h1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-03-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "h1a2b3c4d5e6"
down_revision: Union[str, None] = "g9b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(bind, name: str) -> bool:
    r = bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:n"), {"n": name})
    return r.fetchone() is not None


def _index_exists(bind, name: str) -> bool:
    r = bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type='index' AND name=:n"), {"n": name})
    return r.fetchone() is not None


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "gl_journals"):
        op.create_table(
            "gl_journals",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("reconciliation_group_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
            sa.Column("journal_date", sa.DateTime(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="HKD"),
            sa.Column("voucher_no", sa.String(), nullable=False),
            sa.Column("narration", sa.Text(), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="recon_match"),
            sa.Column("reversal_of_journal_id", sa.String(), nullable=True),
            sa.Column("balancing_account_code", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
            sa.Column("posted_at", sa.DateTime(), nullable=True),
            sa.Column("posted_by", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["reconciliation_group_id"], ["reconciliation_groups.id"]),
            sa.ForeignKeyConstraint(["reversal_of_journal_id"], ["gl_journals.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _index_exists(bind, "ix_gl_journals_company_id"):
        op.create_index("ix_gl_journals_company_id", "gl_journals", ["company_id"], unique=False)
    if not _index_exists(bind, "ix_gl_journals_reconciliation_group_id"):
        op.create_index("ix_gl_journals_reconciliation_group_id", "gl_journals", ["reconciliation_group_id"], unique=False)
    if not _index_exists(bind, "ix_gl_journals_voucher_no"):
        op.create_index("ix_gl_journals_voucher_no", "gl_journals", ["voucher_no"], unique=False)
    if not _index_exists(bind, "ix_gl_journals_reversal_of_journal_id"):
        op.create_index("ix_gl_journals_reversal_of_journal_id", "gl_journals", ["reversal_of_journal_id"], unique=False)
    if not _index_exists(bind, "ix_gl_journal_company_group"):
        op.create_index("ix_gl_journal_company_group", "gl_journals", ["company_id", "reconciliation_group_id"], unique=False)

    if not _table_exists(bind, "gl_journal_lines"):
        op.create_table(
            "gl_journal_lines",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("journal_id", sa.String(), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False),
            sa.Column("account_code", sa.String(), nullable=False),
            sa.Column("debit", sa.Float(), nullable=False, server_default="0"),
            sa.Column("credit", sa.Float(), nullable=False, server_default="0"),
            sa.Column("memo", sa.Text(), nullable=True),
            sa.Column("bank_txn_id", sa.String(), nullable=True),
            sa.Column("ledger_txn_id", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["journal_id"], ["gl_journals.id"]),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _index_exists(bind, "ix_gl_journal_lines_journal_id"):
        op.create_index("ix_gl_journal_lines_journal_id", "gl_journal_lines", ["journal_id"], unique=False)
    if not _index_exists(bind, "ix_gl_journal_lines_account_code"):
        op.create_index("ix_gl_journal_lines_account_code", "gl_journal_lines", ["account_code"], unique=False)
    if not _index_exists(bind, "ix_gl_journal_line_journal"):
        op.create_index("ix_gl_journal_line_journal", "gl_journal_lines", ["journal_id", "line_no"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "gl_journal_lines"):
        op.drop_table("gl_journal_lines")
    if _table_exists(bind, "gl_journals"):
        op.drop_table("gl_journals")

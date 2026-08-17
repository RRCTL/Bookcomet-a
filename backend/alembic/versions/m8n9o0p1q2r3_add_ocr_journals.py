"""add ocr_journals and ocr_journal_lines

Revision ID: m8n9o0p1q2r3
Revises: k5m6n7o8p9q0
Create Date: 2026-04-10 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "m8n9o0p1q2r3"
down_revision: Union[str, None] = "k5m6n7o8p9q0"
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

    if not _table_exists(bind, "ocr_journals"):
        op.create_table(
            "ocr_journals",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=True),
            sa.Column("source", sa.String(length=8), nullable=False),
            sa.Column("source_txn_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="draft"),
            sa.Column("journal_date", sa.DateTime(), nullable=False),
            sa.Column("currency", sa.String(length=8), nullable=False, server_default="HKD"),
            sa.Column("voucher_no", sa.String(), nullable=False),
            sa.Column("narration", sa.Text(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
            sa.ForeignKeyConstraint(["task_id"], ["chat_tasks.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("company_id", "source", "source_txn_id", name="uq_ocr_journal_company_source_txn"),
        )

    if not _index_exists(bind, "ix_ocr_journals_company_id"):
        op.create_index("ix_ocr_journals_company_id", "ocr_journals", ["company_id"], unique=False)
    if not _index_exists(bind, "ix_ocr_journals_task_id"):
        op.create_index("ix_ocr_journals_task_id", "ocr_journals", ["task_id"], unique=False)
    if not _index_exists(bind, "ix_ocr_journals_source_txn_id"):
        op.create_index("ix_ocr_journals_source_txn_id", "ocr_journals", ["source_txn_id"], unique=False)
    if not _index_exists(bind, "ix_ocr_journals_voucher_no"):
        op.create_index("ix_ocr_journals_voucher_no", "ocr_journals", ["voucher_no"], unique=False)
    if not _index_exists(bind, "ix_ocr_journal_company_task"):
        op.create_index("ix_ocr_journal_company_task", "ocr_journals", ["company_id", "task_id"], unique=False)

    if not _table_exists(bind, "ocr_journal_lines"):
        op.create_table(
            "ocr_journal_lines",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("journal_id", sa.String(), nullable=False),
            sa.Column("line_no", sa.Integer(), nullable=False),
            sa.Column("account_code", sa.String(), nullable=False),
            sa.Column("debit", sa.Float(), nullable=False, server_default="0"),
            sa.Column("credit", sa.Float(), nullable=False, server_default="0"),
            sa.Column("memo", sa.Text(), nullable=True),
            sa.ForeignKeyConstraint(["journal_id"], ["ocr_journals.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if not _index_exists(bind, "ix_ocr_journal_lines_journal_id"):
        op.create_index("ix_ocr_journal_lines_journal_id", "ocr_journal_lines", ["journal_id"], unique=False)
    if not _index_exists(bind, "ix_ocr_journal_line_journal"):
        op.create_index("ix_ocr_journal_line_journal", "ocr_journal_lines", ["journal_id", "line_no"], unique=False)
    if not _index_exists(bind, "ix_ocr_journal_lines_account_code"):
        op.create_index("ix_ocr_journal_lines_account_code", "ocr_journal_lines", ["account_code"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "ocr_journal_lines"):
        op.drop_table("ocr_journal_lines")
    if _table_exists(bind, "ocr_journals"):
        op.drop_table("ocr_journals")

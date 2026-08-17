"""add company_id to bank_transactions

Aligns SQLite/Postgres schema with BankTransaction model (company-scoped rows).

Revision ID: s1a2b3c4d5e6
Revises: r8a9b0c1d2e4
Create Date: 2026-04-28

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

revision: str = "s1a2b3c4d5e6"
down_revision: Union[str, None] = "r8a9b0c1d2e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

FK_NAME = "fk_bank_transactions_company_id"
IX_NAME = "ix_bank_transactions_company_id"


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(conn)
    if table_name not in insp.get_table_names():
        return False
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def _index_exists(conn, table_name: str, index_name: str) -> bool:
    insp = sa.inspect(conn)
    for ix in insp.get_indexes(table_name):
        if ix.get("name") == index_name:
            return True
    return False


def _backfill_company_id(conn) -> None:
    """Assign every existing bank_transaction to a company row (required for NOT NULL + FK)."""
    r = conn.execute(
        text("SELECT id FROM companies ORDER BY created_at LIMIT 1")
    ).fetchone()
    cid: str | None
    if r:
        cid = r[0]
    else:
        n = conn.execute(text("SELECT COUNT(*) FROM bank_transactions")).scalar() or 0
        if int(n) > 0:
            cid = "default"
            conn.execute(
                text(
                    "INSERT INTO companies (id, name) VALUES (:id, :name)"
                ),
                {"id": cid, "name": "Default (migration)"},
            )
        else:
            cid = None

    if cid is not None:
        conn.execute(
            text(
                "UPDATE bank_transactions SET company_id = :c "
                "WHERE company_id IS NULL"
            ),
            {"c": cid},
        )


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "bank_transactions" not in insp.get_table_names():
        return
    if _column_exists(bind, "bank_transactions", "company_id"):
        return

    op.add_column(
        "bank_transactions",
        sa.Column("company_id", sa.String(), nullable=True),
    )
    _backfill_company_id(bind)

    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(
            "bank_transactions",
            reflect_kwargs={"resolve_fks": False},
        ) as batch_op:
            batch_op.alter_column(
                "company_id",
                existing_type=sa.String(),
                nullable=False,
            )
            batch_op.create_foreign_key(FK_NAME, "companies", ["company_id"], ["id"])
    else:
        op.alter_column(
            "bank_transactions",
            "company_id",
            existing_type=sa.String(),
            nullable=False,
        )
        op.create_foreign_key(
            FK_NAME,
            "bank_transactions",
            "companies",
            ["company_id"],
            ["id"],
        )

    if not _index_exists(bind, "bank_transactions", IX_NAME):
        op.create_index(IX_NAME, "bank_transactions", ["company_id"], unique=False)


def downgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "bank_transactions", "company_id"):
        return

    if _index_exists(bind, "bank_transactions", IX_NAME):
        op.drop_index(IX_NAME, table_name="bank_transactions")

    dialect = bind.dialect.name
    if dialect == "sqlite":
        with op.batch_alter_table(
            "bank_transactions",
            reflect_kwargs={"resolve_fks": False},
        ) as batch_op:
            batch_op.drop_constraint(FK_NAME, type_="foreignkey")
            batch_op.drop_column("company_id")
    else:
        op.drop_constraint(FK_NAME, "bank_transactions", type_="foreignkey")
        op.drop_column("bank_transactions", "company_id")

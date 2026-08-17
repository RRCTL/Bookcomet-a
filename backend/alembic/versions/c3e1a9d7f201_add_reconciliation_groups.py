"""add_reconciliation_groups

Revision ID: c3e1a9d7f201
Revises: f7cde0f4bc45
Create Date: 2026-03-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c3e1a9d7f201'
down_revision: Union[str, None] = 'f7cde0f4bc45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    from sqlalchemy import text
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name},
    )
    return result.fetchone() is not None


def _column_exists(conn, table_name: str, column_name: str) -> bool:
    from sqlalchemy import text
    result = conn.execute(text(f"PRAGMA table_info({table_name})"))
    return any(row[1] == column_name for row in result.fetchall())


def _index_exists(conn, index_name: str) -> bool:
    from sqlalchemy import text
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name=:i"),
        {"i": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    bind = op.get_bind()

    # Create reconciliation_groups table only if it doesn't exist yet
    # (SQLAlchemy create_all may have already created it)
    if not _table_exists(bind, 'reconciliation_groups'):
        op.create_table(
            'reconciliation_groups',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('company_id', sa.String(), nullable=False),
            sa.Column('trace_id', sa.String(), nullable=True),
            sa.Column('match_cardinality', sa.String(), nullable=False),
            sa.Column('total_bank_amount', sa.Float(), nullable=False),
            sa.Column('total_ledger_amount', sa.Float(), nullable=False),
            sa.Column('difference', sa.Float(), nullable=False, server_default='0.0'),
            sa.Column('partial_remainder_txn_id', sa.String(), nullable=True),
            sa.Column('created_by', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    if not _index_exists(bind, 'ix_reconciliation_groups_company_id'):
        op.create_index('ix_reconciliation_groups_company_id', 'reconciliation_groups', ['company_id'], unique=False)
    if not _index_exists(bind, 'ix_reconciliation_groups_trace_id'):
        op.create_index('ix_reconciliation_groups_trace_id', 'reconciliation_groups', ['trace_id'], unique=False)

    # Add group_id column to reconciliation_match (idempotent)
    if not _column_exists(bind, 'reconciliation_match', 'group_id'):
        op.add_column(
            'reconciliation_match',
            sa.Column('group_id', sa.String(), nullable=True),
        )
    if not _index_exists(bind, 'ix_recon_match_group_id'):
        op.create_index('ix_recon_match_group_id', 'reconciliation_match', ['group_id'], unique=False)

    # Make bank_txn_id and ledger_txn_id nullable in reconciliation_match
    # SQLite does not support ALTER COLUMN directly; use batch mode
    with op.batch_alter_table('reconciliation_match') as batch_op:
        batch_op.alter_column('bank_txn_id', existing_type=sa.String(), nullable=True)
        batch_op.alter_column('ledger_txn_id', existing_type=sa.String(), nullable=True)

    # Extend MatchType enum with new values.
    # SQLite enums are stored as VARCHAR — no ALTER TYPE needed.
    # For PostgreSQL, run:
    #   op.execute("ALTER TYPE matchtype ADD VALUE IF NOT EXISTS 'one_many'")
    #   op.execute("ALTER TYPE matchtype ADD VALUE IF NOT EXISTS 'many_one'")
    #   op.execute("ALTER TYPE matchtype ADD VALUE IF NOT EXISTS 'many_many'")


def downgrade() -> None:
    with op.batch_alter_table('reconciliation_match') as batch_op:
        batch_op.alter_column('bank_txn_id', existing_type=sa.String(), nullable=False)
        batch_op.alter_column('ledger_txn_id', existing_type=sa.String(), nullable=False)
        batch_op.drop_column('group_id')

    op.drop_index('ix_recon_match_group_id', table_name='reconciliation_match')
    op.drop_index('ix_reconciliation_groups_trace_id', table_name='reconciliation_groups')
    op.drop_index('ix_reconciliation_groups_company_id', table_name='reconciliation_groups')
    op.drop_table('reconciliation_groups')

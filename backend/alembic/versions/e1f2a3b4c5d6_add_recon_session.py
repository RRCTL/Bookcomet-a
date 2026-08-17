"""add_recon_session

Revision ID: e1f2a3b4c5d6
Revises: c3e1a9d7f201
Create Date: 2026-03-08 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, tuple, None] = ('c3e1a9d7f201', 'a1b2c3d4e5f6')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    from sqlalchemy import text
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"),
        {"t": table_name},
    )
    return result.fetchone() is not None


def _index_exists(conn, index_name: str) -> bool:
    from sqlalchemy import text
    result = conn.execute(
        text("SELECT name FROM sqlite_master WHERE type='index' AND name=:i"),
        {"i": index_name},
    )
    return result.fetchone() is not None


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, 'recon_session_transactions'):
        op.create_table(
            'recon_session_transactions',
            sa.Column('id', sa.String(), nullable=False),
            sa.Column('company_id', sa.String(), nullable=False),
            sa.Column('txn_id', sa.String(), nullable=False),
            sa.Column('txn_type', sa.String(), nullable=False),
            sa.Column('raw_txn_data', sa.JSON(), nullable=True),
            sa.Column('display_row', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )

    if not _index_exists(bind, 'ix_recon_session_company_id'):
        op.create_index('ix_recon_session_company_id', 'recon_session_transactions', ['company_id'], unique=False)
    if not _index_exists(bind, 'ix_recon_session_txn_id'):
        op.create_index('ix_recon_session_txn_id', 'recon_session_transactions', ['txn_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_recon_session_txn_id', table_name='recon_session_transactions')
    op.drop_index('ix_recon_session_company_id', table_name='recon_session_transactions')
    op.drop_table('recon_session_transactions')

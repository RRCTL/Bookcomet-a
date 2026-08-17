"""add_chat_persistence

Revision ID: e9f0a1b2c3d4
Revises: d5e6f7a8b9c0
Create Date: 2026-03-11 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e9f0a1b2c3d4'
down_revision: Union[str, None] = 'd5e6f7a8b9c0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'chat_tasks',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('company_id', sa.String(), sa.ForeignKey('companies.id'), nullable=False),
        sa.Column('owner_user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.Text(), nullable=True),
        sa.Column('processing_mode', sa.String(10), nullable=True),
        sa.Column('status', sa.String(20), nullable=True, server_default='idle'),
        sa.Column('is_shared_to_company', sa.Boolean(), nullable=True, server_default=sa.text('0')),
        sa.Column('file_count', sa.Integer(), nullable=True, server_default=sa.text('0')),
        sa.Column('page_count', sa.Integer(), nullable=True, server_default=sa.text('0')),
        sa.Column('has_spreadsheet', sa.Boolean(), nullable=True, server_default=sa.text('0')),
        sa.Column('bank_batch_ids', sa.JSON(), nullable=True),
        sa.Column('ledger_batch_ids', sa.JSON(), nullable=True),
        sa.Column('dup_warning', sa.Text(), nullable=True),
        sa.Column('title_generated', sa.Boolean(), nullable=True, server_default=sa.text('0')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_chat_tasks_company_deleted', 'chat_tasks', ['company_id', 'deleted_at'], unique=False)
    op.create_index(op.f('ix_chat_tasks_owner_user_id'), 'chat_tasks', ['owner_user_id'], unique=False)

    op.create_table(
        'task_messages',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), sa.ForeignKey('chat_tasks.id'), nullable=False),
        sa.Column('sequence_index', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(20), nullable=True),
        sa.Column('content_text', sa.Text(), nullable=True),
        sa.Column('content_type', sa.String(30), nullable=True, server_default='text'),
        sa.Column('payload_json', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_task_messages_task_seq', 'task_messages', ['task_id', 'sequence_index'], unique=False)

    op.create_table(
        'task_files',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), sa.ForeignKey('chat_tasks.id'), nullable=False),
        sa.Column('original_filename', sa.Text(), nullable=True),
        sa.Column('storage_path', sa.Text(), nullable=True),
        sa.Column('file_size_bytes', sa.Integer(), nullable=True),
        sa.Column('page_count', sa.Integer(), nullable=True, server_default=sa.text('1')),
        sa.Column('mime_type', sa.String(100), nullable=True),
        sa.Column('import_batch_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.Column('deleted_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_task_files_task_id'), 'task_files', ['task_id'], unique=False)

    op.create_table(
        'task_state_snapshots',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), sa.ForeignKey('chat_tasks.id'), nullable=False),
        sa.Column('state_type', sa.Text(), nullable=True),
        sa.Column('payload_json', sa.JSON(), nullable=True),
        sa.Column('version', sa.Integer(), nullable=True, server_default=sa.text('1')),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_task_state_snapshots_task_id'), 'task_state_snapshots', ['task_id'], unique=False)

    op.create_table(
        'task_audit_log',
        sa.Column('id', sa.String(), nullable=False),
        sa.Column('task_id', sa.String(), sa.ForeignKey('chat_tasks.id'), nullable=False),
        sa.Column('user_id', sa.String(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('action', sa.Text(), nullable=True),
        sa.Column('ip_address', sa.Text(), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_task_audit_log_task_id'), 'task_audit_log', ['task_id'], unique=False)
    op.create_index(op.f('ix_task_audit_log_user_id'), 'task_audit_log', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_task_audit_log_user_id'), table_name='task_audit_log')
    op.drop_index(op.f('ix_task_audit_log_task_id'), table_name='task_audit_log')
    op.drop_table('task_audit_log')

    op.drop_index(op.f('ix_task_state_snapshots_task_id'), table_name='task_state_snapshots')
    op.drop_table('task_state_snapshots')

    op.drop_index(op.f('ix_task_files_task_id'), table_name='task_files')
    op.drop_table('task_files')

    op.drop_index('ix_task_messages_task_seq', table_name='task_messages')
    op.drop_table('task_messages')

    op.drop_index(op.f('ix_chat_tasks_owner_user_id'), table_name='chat_tasks')
    op.drop_index('ix_chat_tasks_company_deleted', table_name='chat_tasks')
    op.drop_table('chat_tasks')

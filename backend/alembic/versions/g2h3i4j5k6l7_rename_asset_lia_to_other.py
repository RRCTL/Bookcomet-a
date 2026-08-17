"""rename_asset_lia_to_other

Rename ASSET_LIA mode values and asset_liability_* schema to OTHER / other_*.

Revision ID: g2h3i4j5k6l7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "g2h3i4j5k6l7"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_MODE_TABLES = (
    ("chat_tasks", "processing_mode"),
    ("company_rule_memories", "mode"),
    ("company_rule_memory_versions", "mode"),
    ("session_summaries", "mode"),
    ("workflow_runs", "processing_mode"),
    ("workflow_folders", "mode"),
    ("workflow_templates", "processing_mode"),
    ("workflow_skills", "mode"),
    ("workflow_skill_versions", "mode"),
    ("workflow_pool2_packages", "processing_mode"),
)


def _table_exists(bind, name: str) -> bool:
    return sa.inspect(bind).has_table(name)


def _column_exists(bind, table: str, column: str) -> bool:
    if not _table_exists(bind, table):
        return False
    return any(col["name"] == column for col in sa.inspect(bind).get_columns(table))


def _backfill_modes(bind, old: str, new: str) -> None:
    for table, column in _MODE_TABLES:
        if not _column_exists(bind, table, column):
            continue
        op.execute(
            sa.text(f"UPDATE {table} SET {column} = :new WHERE {column} = :old").bindparams(
                old=old, new=new
            )
        )


def _rename_fk_column(table: str, old_col: str, new_col: str) -> None:
    bind = op.get_bind()
    if not _column_exists(bind, table, old_col):
        return
    # recreate=always so SQLite rewrites FKs after parent table rename
    with op.batch_alter_table(
        table,
        recreate="always",
        reflect_kwargs={"resolve_fks": False},
    ) as batch_op:
        batch_op.alter_column(
            old_col,
            new_column_name=new_col,
            existing_type=sa.String(),
            existing_nullable=False,
        )


def upgrade() -> None:
    bind = op.get_bind()
    _backfill_modes(bind, "ASSET_LIA", "OTHER")

    if _table_exists(bind, "asset_liability_records") and not _table_exists(bind, "other_records"):
        op.rename_table("asset_liability_records", "other_records")

    _rename_fk_column("loan_records", "asset_liability_record_id", "other_record_id")
    _rename_fk_column("fixed_assets", "asset_liability_record_id", "other_record_id")


def downgrade() -> None:
    bind = op.get_bind()

    _rename_fk_column("loan_records", "other_record_id", "asset_liability_record_id")
    _rename_fk_column("fixed_assets", "other_record_id", "asset_liability_record_id")

    if _table_exists(bind, "other_records") and not _table_exists(bind, "asset_liability_records"):
        op.rename_table("other_records", "asset_liability_records")

    _backfill_modes(bind, "OTHER", "ASSET_LIA")

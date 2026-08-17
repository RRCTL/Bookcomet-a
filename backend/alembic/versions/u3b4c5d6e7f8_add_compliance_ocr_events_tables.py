"""add ocr_completion_events and audit_package_archives

Compliance / audit trail tables. Previously only existed when
Base.metadata.create_all ran with DB_AUTO_CREATE_ON_STARTUP in local mode.

Revision ID: u3b4c5d6e7f8
Revises: t2b3c4d5e6f7
Create Date: 2026-05-04

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u3b4c5d6e7f8"
down_revision: Union[str, None] = "t2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    insp = sa.inspect(conn)
    return table_name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "ocr_completion_events"):
        op.create_table(
            "ocr_completion_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("trace_id", sa.String(), nullable=False),
            sa.Column("filename", sa.String(), nullable=True),
            sa.Column("stage", sa.String(), nullable=False),
            sa.Column("source", sa.String(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_ocr_completion_events_company_id",
            "ocr_completion_events",
            ["company_id"],
            unique=False,
        )
        op.create_index(
            "ix_ocr_completion_events_trace_id",
            "ocr_completion_events",
            ["trace_id"],
            unique=False,
        )
        op.create_index(
            "ix_ocr_completion_events_stage",
            "ocr_completion_events",
            ["stage"],
            unique=False,
        )
        op.create_index(
            "ix_ocr_completion_events_source",
            "ocr_completion_events",
            ["source"],
            unique=False,
        )
        op.create_index(
            "ix_ocr_completion_events_created_at",
            "ocr_completion_events",
            ["created_at"],
            unique=False,
        )

    if not _table_exists(bind, "audit_package_archives"):
        op.create_table(
            "audit_package_archives",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("trace_id_filter", sa.String(), nullable=True),
            sa.Column("content_hash", sa.String(), nullable=False),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("immutable_note", sa.Text(), nullable=False),
            sa.Column("created_by", sa.String(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_audit_package_archives_company_id",
            "audit_package_archives",
            ["company_id"],
            unique=False,
        )
        op.create_index(
            "ix_audit_package_archives_trace_id_filter",
            "audit_package_archives",
            ["trace_id_filter"],
            unique=False,
        )
        op.create_index(
            "ix_audit_package_archives_content_hash",
            "audit_package_archives",
            ["content_hash"],
            unique=False,
        )
        op.create_index(
            "ix_audit_package_archives_created_by",
            "audit_package_archives",
            ["created_by"],
            unique=False,
        )
        op.create_index(
            "ix_audit_package_archives_created_at",
            "audit_package_archives",
            ["created_at"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "audit_package_archives"):
        op.drop_index("ix_audit_package_archives_created_at", table_name="audit_package_archives")
        op.drop_index("ix_audit_package_archives_created_by", table_name="audit_package_archives")
        op.drop_index("ix_audit_package_archives_content_hash", table_name="audit_package_archives")
        op.drop_index("ix_audit_package_archives_trace_id_filter", table_name="audit_package_archives")
        op.drop_index("ix_audit_package_archives_company_id", table_name="audit_package_archives")
        op.drop_table("audit_package_archives")
    if _table_exists(bind, "ocr_completion_events"):
        op.drop_index("ix_ocr_completion_events_created_at", table_name="ocr_completion_events")
        op.drop_index("ix_ocr_completion_events_source", table_name="ocr_completion_events")
        op.drop_index("ix_ocr_completion_events_stage", table_name="ocr_completion_events")
        op.drop_index("ix_ocr_completion_events_trace_id", table_name="ocr_completion_events")
        op.drop_index("ix_ocr_completion_events_company_id", table_name="ocr_completion_events")
        op.drop_table("ocr_completion_events")

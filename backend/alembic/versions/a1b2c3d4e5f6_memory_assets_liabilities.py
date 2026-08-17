"""memory_and_assets_liabilities

Adds tables for:
- session_summaries (cross-session AI memory persistence)
- token_usage_log (LLM token tracking)
- asset_liability_records (staging records for ASSET_LIA tasks)
- loan_records / loan_installments (formal loan data)
- fixed_assets / asset_depreciation_schedule (formal asset + depreciation data)
- Widens chat_tasks.processing_mode to VARCHAR(30) for ASSET_LIA

Revision ID: a1b2c3d4e5f6
Revises: f7cde0f4bc45
Create Date: 2026-03-11 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── Widen processing_mode in chat_tasks ───────────────────────────────────
    # SQLite does not support ALTER COLUMN directly; use batch_alter_table.
    # Avoid resolving FK targets (e.g. companies) that may not exist in a
    # migrations-only SQLite database.
    with op.batch_alter_table(
        "chat_tasks",
        reflect_kwargs={"resolve_fks": False},
    ) as batch_op:
        batch_op.alter_column(
            "processing_mode",
            existing_type=sa.String(10),
            type_=sa.String(30),
            existing_nullable=True,
        )

    # ── session_summaries ─────────────────────────────────────────────────────
    op.create_table(
        "session_summaries",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), sa.ForeignKey("chat_tasks.id", ondelete="CASCADE"), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=True, default=0),
        sa.Column("token_estimate", sa.Integer(), nullable=True, default=0),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_session_summaries_company_id", "session_summaries", ["company_id"])
    op.create_index("ix_session_summaries_mode", "session_summaries", ["mode"])
    op.create_index("ix_session_summaries_task_id", "session_summaries", ["task_id"])

    # ── token_usage_log ───────────────────────────────────────────────────────
    op.create_table(
        "token_usage_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=True),
        sa.Column("call_type", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_token_usage_log_company_id", "token_usage_log", ["company_id"])
    op.create_index("ix_token_usage_log_task_id", "token_usage_log", ["task_id"])

    # ── asset_liability_records ───────────────────────────────────────────────
    op.create_table(
        "asset_liability_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("task_id", sa.String(), sa.ForeignKey("chat_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("record_type", sa.String(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("source_task_id", sa.String(), sa.ForeignKey("chat_tasks.id"), nullable=True),
        sa.Column("source_file_id", sa.String(), sa.ForeignKey("task_files.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_liability_records_company_id", "asset_liability_records", ["company_id"])
    op.create_index("ix_asset_liability_records_task_id", "asset_liability_records", ["task_id"])
    op.create_index("ix_asset_liability_records_record_type", "asset_liability_records", ["record_type"])

    # ── loan_records ──────────────────────────────────────────────────────────
    op.create_table(
        "loan_records",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("asset_liability_record_id", sa.String(), sa.ForeignKey("asset_liability_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("loan_reference", sa.String(), nullable=True),
        sa.Column("lender_name", sa.String(), nullable=False, server_default="Unknown"),
        sa.Column("lender_account", sa.String(), nullable=True),
        sa.Column("principal_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("currency", sa.String(), nullable=False, server_default="HKD"),
        sa.Column("interest_rate_pct", sa.Float(), nullable=True),
        sa.Column("tenor_months", sa.Integer(), nullable=True),
        sa.Column("monthly_installment", sa.Float(), nullable=True),
        sa.Column("start_date", sa.DateTime(), nullable=True),
        sa.Column("maturity_date", sa.DateTime(), nullable=True),
        sa.Column("first_payment_date", sa.DateTime(), nullable=True),
        sa.Column("outstanding_principal", sa.Float(), nullable=True),
        sa.Column("status", sa.String(), nullable=True, server_default="active"),
        sa.Column("memo", sa.Text(), nullable=True),
        sa.Column("document_type", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_liability_record_id"),
    )
    op.create_index("ix_loan_records_company_id", "loan_records", ["company_id"])
    op.create_index("ix_loan_records_asset_liability_record_id", "loan_records", ["asset_liability_record_id"])

    # ── loan_installments ─────────────────────────────────────────────────────
    op.create_table(
        "loan_installments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("loan_id", sa.String(), sa.ForeignKey("loan_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("installment_number", sa.Integer(), nullable=False),
        sa.Column("due_date", sa.DateTime(), nullable=False),
        sa.Column("principal_portion", sa.Float(), nullable=False, server_default="0"),
        sa.Column("interest_portion", sa.Float(), nullable=False, server_default="0"),
        sa.Column("total_payment", sa.Float(), nullable=False, server_default="0"),
        sa.Column("outstanding_principal_after", sa.Float(), nullable=True),
        sa.Column("bank_txn_id_principal", sa.String(), sa.ForeignKey("bank_transactions.id"), nullable=True),
        sa.Column("bank_txn_id_interest", sa.String(), sa.ForeignKey("bank_transactions.id"), nullable=True),
        sa.Column("ledger_txn_id_interest", sa.String(), sa.ForeignKey("ledger_transactions.id"), nullable=True),
        sa.Column("status", sa.String(), nullable=True, server_default="pending"),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_loan_installments_loan_id", "loan_installments", ["loan_id"])
    op.create_index("ix_loan_installments_company_id", "loan_installments", ["company_id"])
    op.create_index("ix_loan_installments_due_date", "loan_installments", ["due_date"])
    op.create_index("idx_loan_installment_due", "loan_installments", ["loan_id", "due_date"])

    # ── fixed_assets ──────────────────────────────────────────────────────────
    op.create_table(
        "fixed_assets",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("asset_liability_record_id", sa.String(), sa.ForeignKey("asset_liability_records.id", ondelete="CASCADE"), nullable=False),
        sa.Column("asset_reference", sa.String(), nullable=True),
        sa.Column("asset_name", sa.String(), nullable=False, server_default="Unknown Asset"),
        sa.Column("asset_type", sa.String(), nullable=False, server_default="equipment"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("purchase_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("acquisition_date", sa.DateTime(), nullable=True),
        sa.Column("currency", sa.String(), nullable=False, server_default="HKD"),
        sa.Column("vendor", sa.String(), nullable=True),
        sa.Column("invoice_ref", sa.String(), nullable=True),
        sa.Column("useful_life_months", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("residual_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("depreciation_method", sa.String(), nullable=False, server_default="straight_line"),
        sa.Column("accumulated_depreciation", sa.Float(), nullable=False, server_default="0"),
        sa.Column("net_book_value", sa.Float(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=True, server_default="active"),
        sa.Column("disposal_date", sa.DateTime(), nullable=True),
        sa.Column("disposal_amount", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("asset_liability_record_id"),
    )
    op.create_index("ix_fixed_assets_company_id", "fixed_assets", ["company_id"])
    op.create_index("ix_fixed_assets_asset_liability_record_id", "fixed_assets", ["asset_liability_record_id"])

    # ── asset_depreciation_schedule ───────────────────────────────────────────
    op.create_table(
        "asset_depreciation_schedule",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("asset_id", sa.String(), sa.ForeignKey("fixed_assets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("company_id", sa.String(), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=False),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("period_type", sa.String(), nullable=True, server_default="monthly"),
        sa.Column("depreciation_amount", sa.Float(), nullable=False, server_default="0"),
        sa.Column("accumulated_at_period_end", sa.Float(), nullable=False, server_default="0"),
        sa.Column("net_book_value_at_period_end", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ledger_txn_id", sa.String(), sa.ForeignKey("ledger_transactions.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_asset_depreciation_schedule_asset_id", "asset_depreciation_schedule", ["asset_id"])
    op.create_index("ix_asset_depreciation_schedule_company_id", "asset_depreciation_schedule", ["company_id"])
    op.create_index("ix_asset_depreciation_schedule_period_start", "asset_depreciation_schedule", ["period_start"])
    op.create_index("idx_asset_depr_period", "asset_depreciation_schedule", ["asset_id", "period_start"])


def downgrade() -> None:
    op.drop_index("idx_asset_depr_period", table_name="asset_depreciation_schedule")
    op.drop_index("ix_asset_depreciation_schedule_period_start", table_name="asset_depreciation_schedule")
    op.drop_index("ix_asset_depreciation_schedule_company_id", table_name="asset_depreciation_schedule")
    op.drop_index("ix_asset_depreciation_schedule_asset_id", table_name="asset_depreciation_schedule")
    op.drop_table("asset_depreciation_schedule")

    op.drop_index("ix_fixed_assets_asset_liability_record_id", table_name="fixed_assets")
    op.drop_index("ix_fixed_assets_company_id", table_name="fixed_assets")
    op.drop_table("fixed_assets")

    op.drop_index("idx_loan_installment_due", table_name="loan_installments")
    op.drop_index("ix_loan_installments_due_date", table_name="loan_installments")
    op.drop_index("ix_loan_installments_company_id", table_name="loan_installments")
    op.drop_index("ix_loan_installments_loan_id", table_name="loan_installments")
    op.drop_table("loan_installments")

    op.drop_index("ix_loan_records_asset_liability_record_id", table_name="loan_records")
    op.drop_index("ix_loan_records_company_id", table_name="loan_records")
    op.drop_table("loan_records")

    op.drop_index("ix_asset_liability_records_record_type", table_name="asset_liability_records")
    op.drop_index("ix_asset_liability_records_task_id", table_name="asset_liability_records")
    op.drop_index("ix_asset_liability_records_company_id", table_name="asset_liability_records")
    op.drop_table("asset_liability_records")

    op.drop_index("ix_token_usage_log_task_id", table_name="token_usage_log")
    op.drop_index("ix_token_usage_log_company_id", table_name="token_usage_log")
    op.drop_table("token_usage_log")

    op.drop_index("ix_session_summaries_task_id", table_name="session_summaries")
    op.drop_index("ix_session_summaries_mode", table_name="session_summaries")
    op.drop_index("ix_session_summaries_company_id", table_name="session_summaries")
    op.drop_table("session_summaries")

    with op.batch_alter_table("chat_tasks") as batch_op:
        batch_op.alter_column(
            "processing_mode",
            existing_type=sa.String(30),
            type_=sa.String(10),
            existing_nullable=True,
        )

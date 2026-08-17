"""add company_subscriptions, user_subscriptions, billing_webhook_events

These tables were previously only created via Base.metadata.create_all when
DB_AUTO_CREATE_ON_STARTUP ran in local mode; Alembic-only databases lacked them.

Revision ID: p1a2b3c4d5e6
Revises: n7o8p9q0r1s2
Create Date: 2026-04-28

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p1a2b3c4d5e6"
down_revision: Union[str, None] = "n7o8p9q0r1s2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(conn, table_name: str) -> bool:
    insp = sa.inspect(conn)
    return table_name in insp.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()

    if not _table_exists(bind, "company_subscriptions"):
        op.create_table(
            "company_subscriptions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("company_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="trial"),
            sa.Column("plan_type", sa.String(), nullable=False, server_default="monthly"),
            sa.Column("plan_tier", sa.String(), nullable=False, server_default="starter"),
            sa.Column("trial_ends_at", sa.DateTime(), nullable=True),
            sa.Column("current_period_end", sa.DateTime(), nullable=True),
            sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("payment_provider", sa.String(), nullable=True),
            sa.Column("provider_customer_id", sa.String(), nullable=True),
            sa.Column("provider_subscription_id", sa.String(), nullable=True),
            sa.Column("stripe_customer_id", sa.String(), nullable=True),
            sa.Column("stripe_subscription_id", sa.String(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_company_subscriptions_company_id"),
            "company_subscriptions",
            ["company_id"],
            unique=True,
        )

    if not _table_exists(bind, "user_subscriptions"):
        op.create_table(
            "user_subscriptions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("user_id", sa.String(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="trial"),
            sa.Column("plan_type", sa.String(), nullable=False, server_default="monthly"),
            sa.Column("plan_tier", sa.String(), nullable=False, server_default="starter"),
            sa.Column("trial_ends_at", sa.DateTime(), nullable=True),
            sa.Column("current_period_end", sa.DateTime(), nullable=True),
            sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("payment_provider", sa.String(), nullable=True),
            sa.Column("provider_customer_id", sa.String(), nullable=True),
            sa.Column("provider_subscription_id", sa.String(), nullable=True),
            sa.Column("stripe_customer_id", sa.String(), nullable=True),
            sa.Column("stripe_subscription_id", sa.String(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_user_subscriptions_user_id"),
            "user_subscriptions",
            ["user_id"],
            unique=True,
        )

    if not _table_exists(bind, "billing_webhook_events"):
        op.create_table(
            "billing_webhook_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("provider", sa.String(), nullable=False),
            sa.Column("provider_event_id", sa.String(), nullable=False),
            sa.Column(
                "received_at",
                sa.DateTime(),
                server_default=sa.text("(CURRENT_TIMESTAMP)"),
                nullable=True,
            ),
            sa.Column("process_error", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "provider",
                "provider_event_id",
                name="uq_billing_webhook_provider_event",
            ),
        )
        op.create_index(
            op.f("ix_billing_webhook_events_provider"),
            "billing_webhook_events",
            ["provider"],
            unique=False,
        )
        op.create_index(
            op.f("ix_billing_webhook_events_provider_event_id"),
            "billing_webhook_events",
            ["provider_event_id"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    if _table_exists(bind, "billing_webhook_events"):
        op.drop_index(op.f("ix_billing_webhook_events_provider_event_id"), table_name="billing_webhook_events")
        op.drop_index(op.f("ix_billing_webhook_events_provider"), table_name="billing_webhook_events")
        op.drop_table("billing_webhook_events")
    if _table_exists(bind, "user_subscriptions"):
        op.drop_index(op.f("ix_user_subscriptions_user_id"), table_name="user_subscriptions")
        op.drop_table("user_subscriptions")
    if _table_exists(bind, "company_subscriptions"):
        op.drop_index(op.f("ix_company_subscriptions_company_id"), table_name="company_subscriptions")
        op.drop_table("company_subscriptions")

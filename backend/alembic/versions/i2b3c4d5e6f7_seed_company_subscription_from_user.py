"""seed company subscription from legacy user subscription

Revision ID: i2b3c4d5e6f7
Revises: h1a2b3c4d5e6
Create Date: 2026-03-30

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "i2b3c4d5e6f7"
down_revision: Union[str, None] = "h1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    company_ids = [
        str(r[0])
        for r in conn.execute(sa.text("SELECT DISTINCT company_id FROM memberships")).all()
    ]
    us_by_uid: dict[str, dict] = {}
    try:
        for r in conn.execute(sa.text("SELECT * FROM user_subscriptions")).mappings().all():
            us_by_uid[str(r["user_id"])] = dict(r)
    except Exception:
        pass

    for cid in company_ids:
        owner = conn.execute(
            sa.text(
                "SELECT user_id FROM memberships WHERE company_id = :c AND role = 'owner' LIMIT 1"
            ),
            {"c": cid},
        ).scalar()
        if not owner:
            owner = conn.execute(
                sa.text("SELECT user_id FROM memberships WHERE company_id = :c LIMIT 1"),
                {"c": cid},
            ).scalar()
        us = us_by_uid.get(str(owner)) if owner else None

        cs = conn.execute(
            sa.text(
                "SELECT id, status, stripe_subscription_id FROM company_subscriptions WHERE company_id = :c"
            ),
            {"c": cid},
        ).mappings().first()

        if cs and cs.get("stripe_subscription_id"):
            continue

        def _row_from_us() -> dict:
            """Copy plan/term fields only; do not duplicate one Stripe subscription across companies."""
            u = us or {}
            return {
                "st": u.get("status") or "trial",
                "pt": u.get("plan_type") or "monthly",
                "tier": u.get("plan_tier") or "starter",
                "te": u.get("trial_ends_at"),
                "cpe": u.get("current_period_end"),
                "cat": 1 if u.get("cancel_at_period_end") else 0,
                "pp": None,
                "puci": None,
                "pusi": None,
                "sci": None,
                "ssi": None,
            }

        r = _row_from_us()

        if cs:
            conn.execute(
                sa.text(
                    """
                    UPDATE company_subscriptions SET
                      status = :st, plan_type = :pt, plan_tier = :tier,
                      trial_ends_at = :te, current_period_end = :cpe,
                      cancel_at_period_end = :cat, payment_provider = :pp,
                      provider_customer_id = :puci, provider_subscription_id = :pusi,
                      stripe_customer_id = :sci, stripe_subscription_id = :ssi
                    WHERE company_id = :cid
                    """
                ),
                {**r, "cid": cid},
            )
        else:
            conn.execute(
                sa.text(
                    """
                    INSERT INTO company_subscriptions (
                      id, company_id, status, plan_type, plan_tier,
                      trial_ends_at, current_period_end, cancel_at_period_end,
                      payment_provider, provider_customer_id, provider_subscription_id,
                      stripe_customer_id, stripe_subscription_id
                    ) VALUES (
                      :id, :cid, :st, :pt, :tier,
                      :te, :cpe, :cat,
                      :pp, :puci, :pusi, :sci, :ssi
                    )
                    """
                ),
                {**r, "id": str(uuid.uuid4()), "cid": cid},
            )


def downgrade() -> None:
    pass

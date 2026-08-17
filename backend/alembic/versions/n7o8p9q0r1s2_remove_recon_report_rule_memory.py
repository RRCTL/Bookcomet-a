"""remove RECON/REPORT company_rule_memories (product no longer uses these modes)

Revision ID: n7o8p9q0r1s2
Revises: m8n9o0p1q2r3
Create Date: 2026-04-10

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "n7o8p9q0r1s2"
down_revision: Union[str, None] = "m8n9o0p1q2r3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    # Versions table may not exist on fresh SQLite until runtime create_all.
    if "company_rule_memory_versions" in tables and "company_rule_memories" in tables:
        bind.execute(
            sa.text(
                """
                DELETE FROM company_rule_memory_versions
                WHERE memory_id IN (
                    SELECT id FROM company_rule_memories WHERE mode IN ('RECON', 'REPORT')
                )
                """
            )
        )
    if "company_rule_memories" in tables:
        bind.execute(sa.text("DELETE FROM company_rule_memories WHERE mode IN ('RECON', 'REPORT')"))


def downgrade() -> None:
    pass

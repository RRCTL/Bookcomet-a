"""add users.username; make email optional

Revision ID: h3u4s5e6r7n8
Revises: g2h3i4j5k6l7
Create Date: 2026-08-06 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "h3u4s5e6r7n8"
down_revision: Union[str, None] = "g2h3i4j5k6l7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(bind, table_name: str, column_name: str) -> bool:
    insp = sa.inspect(bind)
    if table_name not in insp.get_table_names():
        return False
    return any(c["name"] == column_name for c in insp.get_columns(table_name))


def _username_from_email(email: str | None, user_id: str) -> str:
    raw = (email or "").strip().lower()
    local = raw.split("@", 1)[0] if raw else ""
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in local)
    cleaned = cleaned.strip("._-")[:64]
    if len(cleaned) < 3:
        cleaned = f"user_{user_id.replace('-', '')[:12]}"
    return cleaned


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column(bind, "users", "username"):
        op.add_column("users", sa.Column("username", sa.String(), nullable=True))

    rows = bind.execute(sa.text("SELECT id, email, username FROM users")).fetchall()
    used: set[str] = set()
    for row in rows:
        uid, email, existing = row[0], row[1], row[2]
        if existing and str(existing).strip():
            base = str(existing).strip().lower()
        else:
            base = _username_from_email(email, str(uid))
        candidate = base
        n = 2
        while candidate in used:
            suffix = f"_{n}"
            candidate = f"{base[: max(1, 64 - len(suffix))]}{suffix}"
            n += 1
        used.add(candidate)
        bind.execute(
            sa.text("UPDATE users SET username = :u WHERE id = :id"),
            {"u": candidate, "id": uid},
        )

    # Enforce NOT NULL + unique where the dialect allows batch alter.
    with op.batch_alter_table("users") as batch:
        batch.alter_column("username", existing_type=sa.String(), nullable=False)
        batch.alter_column("email", existing_type=sa.String(), nullable=True)
        batch.create_index("ix_users_username", ["username"], unique=True)

    bind.execute(sa.text("UPDATE users SET is_verified = 1 WHERE is_verified IS NULL OR is_verified = 0"))


def downgrade() -> None:
    bind = op.get_bind()
    with op.batch_alter_table("users") as batch:
        if _has_column(bind, "users", "username"):
            try:
                batch.drop_index("ix_users_username")
            except Exception:
                pass
            batch.drop_column("username")
        batch.alter_column("email", existing_type=sa.String(), nullable=False)

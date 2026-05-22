"""Replace dead pairings table with user-scoped mobile_devices registry.

The old `pairings` table was only ever populated by tests; its
``mobile_device_id`` column conflated user-id and device-id. Drop it and
introduce ``mobile_devices`` keyed by a client-generated per-device UUID,
with a user-scoped FK so revoking one device does not affect the user's
other devices.

Pre-production: ``mobile_refresh_tokens`` rows are also cleared so existing
sessions (which carry the old conflated value) re-pair on next refresh.

Mirrors the runtime migration in ``central_server/db/database.py`` guarded
by ``PRAGMA user_version < 13``.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM mobile_refresh_tokens")
    op.drop_index("idx_pairings_agent", table_name="pairings", if_exists=True)
    op.drop_table("pairings")

    op.create_table(
        "mobile_devices",
        sa.Column("device_id", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("last_seen_at", sa.Text(), nullable=True),
        sa.Column("revoked_at", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_mobile_devices_user",
        "mobile_devices",
        ["user_id"],
        sqlite_where=sa.text("revoked_at IS NULL"),
    )

    # Index leading on ``mobile_device_id`` so the device-scoped revoke
    # cascade in ``revoke_all_refresh_tokens_for_device`` is a partial-
    # index probe instead of a full ``mobile_refresh_tokens`` scan. The
    # existing ``idx_refresh_agent_device(agent_id, mobile_device_id)``
    # cannot serve a ``WHERE mobile_device_id = ?`` predicate
    # efficiently.
    op.create_index(
        "idx_refresh_mobile_device",
        "mobile_refresh_tokens",
        ["mobile_device_id"],
        sqlite_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_refresh_mobile_device", table_name="mobile_refresh_tokens"
    )
    op.drop_index("idx_mobile_devices_user", table_name="mobile_devices")
    op.drop_table("mobile_devices")

    op.create_table(
        "pairings",
        sa.Column("pairing_id", sa.Text(), primary_key=True),
        sa.Column(
            "agent_id",
            sa.Text(),
            sa.ForeignKey("agents.agent_id"),
            nullable=False,
        ),
        sa.Column("mobile_device_id", sa.Text(), nullable=False),
        sa.Column("device_name", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_pairings_agent",
        "pairings",
        ["agent_id"],
        sqlite_where=sa.text("revoked_at IS NULL"),
    )

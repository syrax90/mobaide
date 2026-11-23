"""Cloud voice input: add the voice_engine_choices and cloud_voice_usage tables.

Mirrors the runtime migration in ``central_server/db/database.py`` (guarded by
``PRAGMA user_version < 24``). Alembic is the out-of-band migration path for
environments that prefer it; the runtime block remains authoritative for
default deployments.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-22
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022"
down_revision: Union[str, None] = "0021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "voice_engine_choices",
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("agent_id", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "agent_id"),
    )
    op.create_table(
        "cloud_voice_usage",
        sa.Column("user_id", sa.Text(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("day", sa.Text(), nullable=False),
        sa.Column("seconds", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("requests", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("user_id", "day"),
    )
    op.create_index("idx_cloud_voice_usage_day", "cloud_voice_usage", ["day"])


def downgrade() -> None:
    op.drop_index("idx_cloud_voice_usage_day", table_name="cloud_voice_usage")
    op.drop_table("cloud_voice_usage")
    op.drop_table("voice_engine_choices")

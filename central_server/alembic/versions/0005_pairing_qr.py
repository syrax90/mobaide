"""QR pairing: relax pairing_tokens.agent_id, add claim columns.

Mirrors the runtime migration in ``central_server/db/database.py`` (guarded
by ``PRAGMA user_version < 7``). Alembic is the out-of-band migration path
for environments that prefer it; the runtime block remains authoritative
for default deployments.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("pairing_tokens") as batch:
        batch.alter_column(
            "agent_id",
            existing_type=sa.Text(),
            existing_nullable=False,
            nullable=True,
        )
        batch.add_column(sa.Column("claimed_token_raw", sa.Text(), nullable=True))
        batch.add_column(sa.Column("claimed_agent_id", sa.Text(), nullable=True))
        batch.add_column(sa.Column("agent_metadata", sa.Text(), nullable=True))
        batch.create_foreign_key(
            "fk_pairing_tokens_claimed_agent",
            "agents",
            ["claimed_agent_id"],
            ["agent_id"],
        )
    op.create_index(
        "idx_pairing_tokens_claimed_agent",
        "pairing_tokens",
        ["claimed_agent_id"],
        sqlite_where=sa.text("claimed_agent_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_pairing_tokens_claimed_agent", table_name="pairing_tokens"
    )
    with op.batch_alter_table("pairing_tokens") as batch:
        batch.drop_constraint(
            "fk_pairing_tokens_claimed_agent", type_="foreignkey"
        )
        batch.drop_column("agent_metadata")
        batch.drop_column("claimed_agent_id")
        batch.drop_column("claimed_token_raw")
        batch.alter_column(
            "agent_id",
            existing_type=sa.Text(),
            existing_nullable=True,
            nullable=False,
        )

"""Password reset: add the password_reset_tokens table.

Mirrors the runtime migration in ``central_server/db/database.py`` (guarded by
``PRAGMA user_version < 10``). Alembic is the out-of-band migration path for
environments that prefer it; the runtime block remains authoritative for
default deployments.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("consumed_at", sa.Text(), nullable=True),
        sa.Column(
            "attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "idx_password_reset_user",
        "password_reset_tokens",
        ["user_id"],
        sqlite_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_password_reset_user",
        table_name="password_reset_tokens",
    )
    op.drop_table("password_reset_tokens")

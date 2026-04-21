"""Email verification: add columns + tokens table and backfill existing users.

Mirrors the runtime migration in ``central_server/db/database.py`` (guarded by
``PRAGMA user_version < 5``). Alembic is the out-of-band migration path for
environments that prefer it; the runtime block remains authoritative for
default deployments.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add email_verified + verified_at to users.
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "email_verified",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch.add_column(sa.Column("verified_at", sa.Text(), nullable=True))

    # Backfill: grandfather pre-existing users so they can still log in.
    op.execute("UPDATE users SET email_verified = 1")

    # New table: email_verification_tokens.
    op.create_table(
        "email_verification_tokens",
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
        "idx_email_verification_user",
        "email_verification_tokens",
        ["user_id"],
        sqlite_where=sa.text("consumed_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_email_verification_user",
        table_name="email_verification_tokens",
    )
    op.drop_table("email_verification_tokens")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("verified_at")
        batch.drop_column("email_verified")

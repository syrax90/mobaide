"""Saved keyboard shortcuts: add the user_shortcuts table.

Mirrors the runtime migration in ``central_server/db/database.py`` (guarded by
``PRAGMA user_version < 16``). Alembic is the out-of-band migration path for
environments that prefer it; the runtime block remains authoritative for
default deployments.

Revision ID: 0014
Revises: 0013
Create Date: 2026-07-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_shortcuts",
        sa.Column("shortcut_id", sa.Text(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Text(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("chord_token", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.Text(), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default="1",
        ),
    )
    op.create_index(
        "idx_user_shortcuts_owner",
        "user_shortcuts",
        ["owner_user_id"],
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_user_shortcuts_dedupe",
        "user_shortcuts",
        ["owner_user_id", "kind", "chord_token"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_user_shortcuts_dedupe",
        table_name="user_shortcuts",
    )
    op.drop_index(
        "idx_user_shortcuts_owner",
        table_name="user_shortcuts",
    )
    op.drop_table("user_shortcuts")

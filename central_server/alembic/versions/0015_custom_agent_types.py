"""Custom AI agent types: add the custom_agent_types table.

Mirrors the runtime migration in ``central_server/db/database.py`` (guarded by
``PRAGMA user_version < 17``). Alembic is the out-of-band migration path for
environments that prefer it; the runtime block remains authoritative for
default deployments.

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "custom_agent_types",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Text(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("command", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.Column("deleted_at", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_custom_agent_types_owner",
        "custom_agent_types",
        ["owner_user_id"],
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "idx_custom_agent_types_owner_name",
        "custom_agent_types",
        ["owner_user_id", sa.text("name COLLATE NOCASE")],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_custom_agent_types_owner_name",
        table_name="custom_agent_types",
    )
    op.drop_index(
        "idx_custom_agent_types_owner",
        table_name="custom_agent_types",
    )
    op.drop_table("custom_agent_types")

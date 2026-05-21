"""Add deletion_audit.mode (scheduled vs immediate self-delete).

Mirrors the runtime migration in ``central_server/db/database.py`` (guarded by
``PRAGMA user_version < 12``). Alembic is the out-of-band migration path for
environments that prefer it; the runtime block remains authoritative for
default deployments.

SQLite ALTER TABLE cannot attach a CHECK constraint, so the CHECK lives in
``_SCHEMA`` only (fresh DBs). Application-level validation in
``record_deletion_audit`` and ``DeleteAccountRequest`` defends upgraded DBs.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("deletion_audit") as batch:
        batch.add_column(
            sa.Column(
                "mode",
                sa.Text(),
                nullable=False,
                server_default="scheduled",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("deletion_audit") as batch:
        batch.drop_column("mode")

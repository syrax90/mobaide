"""Device revocation reaches issued JWTs: watermark table + session device binding.

Mirrors the runtime migration in ``central_server/db/database.py`` (guarded by
``PRAGMA user_version < 22``). Alembic is the out-of-band migration path for
environments that prefer it; the runtime block remains authoritative for
default deployments.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device_revocations",
        sa.Column("device_id", sa.Text(), primary_key=True),
        sa.Column("tokens_valid_after", sa.Integer(), nullable=False),
    )
    with op.batch_alter_table("user_refresh_tokens") as batch_op:
        batch_op.add_column(sa.Column("device_id", sa.Text(), nullable=True))
    op.execute(
        "INSERT OR IGNORE INTO device_revocations (device_id, tokens_valid_after) "
        "SELECT device_id, CAST(strftime('%s', revoked_at) AS INTEGER) "
        "FROM mobile_devices WHERE revoked_at IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("user_refresh_tokens") as batch_op:
        batch_op.drop_column("device_id")
    op.drop_table("device_revocations")

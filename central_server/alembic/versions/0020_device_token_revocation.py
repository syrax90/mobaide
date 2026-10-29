"""Device revocation reaches issued JWTs: watermark + session device binding.

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
    with op.batch_alter_table("mobile_devices") as batch_op:
        batch_op.add_column(
            sa.Column("tokens_valid_after", sa.Integer(), nullable=True)
        )
    with op.batch_alter_table("user_refresh_tokens") as batch_op:
        batch_op.add_column(sa.Column("device_id", sa.Text(), nullable=True))
    op.execute(
        "UPDATE mobile_devices "
        "SET tokens_valid_after = CAST(strftime('%s', revoked_at) AS INTEGER) "
        "WHERE revoked_at IS NOT NULL AND tokens_valid_after IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("user_refresh_tokens") as batch_op:
        batch_op.drop_column("device_id")
    with op.batch_alter_table("mobile_devices") as batch_op:
        batch_op.drop_column("tokens_valid_after")

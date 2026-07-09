"""Add mobile_devices platform/push_token/push_environment for APNs push.

Mirrors the runtime ``PRAGMA user_version < 15`` migration in
``central_server.db.database``.

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("mobile_devices") as batch_op:
        batch_op.add_column(sa.Column("platform", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("push_token", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("push_environment", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("mobile_devices") as batch_op:
        batch_op.drop_column("push_environment")
        batch_op.drop_column("push_token")
        batch_op.drop_column("platform")

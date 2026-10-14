"""Agent self-uninstall: capability column + pending uninstall requests.

Alembic is the out-of-band migration path for environments that prefer it;
the runtime block remains authoritative for default deployments.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(
            sa.Column(
                "supports_uninstall",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
    op.create_table(
        "agent_uninstall_requests",
        sa.Column("agent_id", sa.Text(), primary_key=True),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("agent_uninstall_requests")
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_column("supports_uninstall")

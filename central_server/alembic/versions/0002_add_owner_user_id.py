"""Add owner_user_id FK column to agents table.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-16
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch_op:
        batch_op.add_column(
            sa.Column("owner_user_id", sa.Text(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_agents_owner_user_id",
            "users",
            ["owner_user_id"],
            ["id"],
        )
    op.create_index("idx_agents_owner", "agents", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("idx_agents_owner", table_name="agents")
    with op.batch_alter_table("agents") as batch_op:
        batch_op.drop_constraint("fk_agents_owner_user_id", type_="foreignkey")
        batch_op.drop_column("owner_user_id")

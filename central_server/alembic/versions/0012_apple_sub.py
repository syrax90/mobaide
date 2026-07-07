"""Add users.apple_sub for Sign in with Apple.

Mirrors the runtime ``PRAGMA user_version < 14`` migration in
``central_server.db.database``. Uniqueness is enforced via a partial unique
index (not an inline UNIQUE) so fresh and upgraded schemas stay identical.

Revision ID: 0012
Revises: 0011
Create Date: 2026-07-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("apple_sub", sa.Text(), nullable=True))
    op.create_index(
        "idx_users_apple_sub",
        "users",
        ["apple_sub"],
        unique=True,
        sqlite_where=sa.text("apple_sub IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_users_apple_sub", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("apple_sub")

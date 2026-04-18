"""Purge admin-owned agents and their dependent rows.

Enforces the invariant that ``admin`` users never own agents. Any pre-existing
agent whose owner has ``role='admin'`` is deleted, along with its pairings,
pairing tokens, and mobile refresh tokens (schema has no ON DELETE CASCADE,
so dependents are removed explicitly).

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-18
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ADMIN_OWNED_AGENT_IDS = (
    "SELECT a.agent_id FROM agents a "
    "JOIN users u ON a.owner_user_id = u.id "
    "WHERE u.role = 'admin'"
)


def upgrade() -> None:
    op.execute(
        f"DELETE FROM mobile_refresh_tokens WHERE agent_id IN ({_ADMIN_OWNED_AGENT_IDS})"
    )
    op.execute(
        f"DELETE FROM pairing_tokens WHERE agent_id IN ({_ADMIN_OWNED_AGENT_IDS})"
    )
    op.execute(
        f"DELETE FROM pairings WHERE agent_id IN ({_ADMIN_OWNED_AGENT_IDS})"
    )
    op.execute(
        "DELETE FROM agents WHERE owner_user_id IN "
        "(SELECT id FROM users WHERE role = 'admin')"
    )


def downgrade() -> None:
    # Deleted rows cannot be restored.
    pass

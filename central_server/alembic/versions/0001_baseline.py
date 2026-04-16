"""Baseline — stamps the schema as of PRAGMA user_version=2.

No DDL is executed; this revision represents the pre-Alembic schema state
(agents, pairings, pairing_tokens, mobile_refresh_tokens, users).

Revision ID: 0001
Revises: None
Create Date: 2026-04-16
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

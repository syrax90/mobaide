"""Persist agent handshake info: agent_version + agent_protocol_version.

Mirrors the runtime migration in ``central_server/db/database.py``
(guarded by ``PRAGMA user_version < 9``). Pure additive ALTER —
both columns are nullable so existing rows survive the upgrade
unchanged. Rows backfill on the next successful agent auth via
``Database.update_agent_handshake_info``.

Why the columns exist: the agent already sends ``agent_version`` and
``protocol_version`` in its WS auth handshake
(``mobaide_agent/mobaide/relay/client.py``), but until now the
central server discarded both. Persisting them lets the admin
dashboard ``Details`` page surface the value (and compare it to the
manifest's latest version) even when the agent is currently offline.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("agent_version", sa.Text(), nullable=True))
        batch.add_column(
            sa.Column("agent_protocol_version", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("agent_protocol_version")
        batch.drop_column("agent_version")

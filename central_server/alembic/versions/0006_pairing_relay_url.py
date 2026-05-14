"""QR pairing: persist relay_url on pairing_tokens.

Mirrors the runtime migration in ``central_server/db/database.py``
(guarded by ``PRAGMA user_version < 8``). Pure additive ALTER —
``relay_url`` is nullable so existing rows survive the upgrade
unchanged; the router falls back to ``request.base_url``-style
resolution for those legacy rows, which time out within the nonce
TTL after upgrade.

Why the column exists: the dispatch handler (``/api/pairing/claim``)
and the WS recovery handler (``/ws/pairing/{nonce}``) used to each
compute ``relay_url`` independently from different request contexts
(``request.base_url`` vs ``websocket.url.scheme``). Behind a reverse
proxy that does not pass ``X-Forwarded-Proto`` to uvicorn, the two
helpers can disagree on the scheme — the agent then refuses its own
credentials on strict-equality validation. Persisting the relay_url
at initiate time pins both branches to the same value.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("pairing_tokens") as batch:
        batch.add_column(sa.Column("relay_url", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("pairing_tokens") as batch:
        batch.drop_column("relay_url")

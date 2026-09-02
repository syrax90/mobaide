"""Google Ads click attribution + first-pairing marker on users.

Alembic is the out-of-band migration path for environments that prefer it;
the runtime block remains authoritative for default deployments (it also
backfills ``first_paired_at`` for accounts that already own an agent, which
this revision does not attempt).

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Batch mode, matching every prior column migration (0017 is the closest
    # twin): SQLite cannot ALTER in place, so Alembic recreates the table.
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("ad_click_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("ad_click_kind", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("ad_click_at", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column(
                "ads_consent",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(sa.Column("ad_attribution_sig", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("first_paired_at", sa.Text(), nullable=True))
    op.create_index(
        "idx_users_ad_attribution_sig",
        "users",
        ["ad_attribution_sig"],
        sqlite_where=sa.text("ad_attribution_sig IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_users_ad_attribution_sig", table_name="users")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("first_paired_at")
        batch_op.drop_column("ad_attribution_sig")
        batch_op.drop_column("ads_consent")
        batch_op.drop_column("ad_click_at")
        batch_op.drop_column("ad_click_kind")
        batch_op.drop_column("ad_click_id")

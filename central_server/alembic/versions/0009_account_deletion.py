"""Account self-deletion: add grace-period columns + audit + cancel-token tables.

Mirrors the runtime migration in ``central_server/db/database.py`` (guarded by
``PRAGMA user_version < 11``). Alembic is the out-of-band migration path for
environments that prefer it; the runtime block remains authoritative for
default deployments.

The ``deletion_audit`` table deliberately has NO foreign key on ``user_id``:
audit rows must outlive the cascade-purge of the user they reference (GDPR
Art. 17 audit trail).

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("scheduled_deletion_at", sa.Text(), nullable=True))
        batch.add_column(sa.Column("deletion_requested_at", sa.Text(), nullable=True))
    op.create_index(
        "idx_users_scheduled_deletion",
        "users",
        ["scheduled_deletion_at"],
        sqlite_where=sa.text("scheduled_deletion_at IS NOT NULL"),
    )

    op.create_table(
        "account_deletion_cancel_tokens",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Text(),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=False),
        sa.Column("consumed_at", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_account_deletion_cancel_user",
        "account_deletion_cancel_tokens",
        ["user_id"],
        sqlite_where=sa.text("consumed_at IS NULL"),
    )

    op.create_table(
        "deletion_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("email_at_request", sa.Text(), nullable=False),
        sa.Column("requested_at", sa.Text(), nullable=False),
        sa.Column("scheduled_for", sa.Text(), nullable=False),
        sa.Column(
            "source",
            sa.Text(),
            sa.CheckConstraint("source IN ('android', 'portal', 'email_link')"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Text(),
            sa.CheckConstraint(
                "status IN ('pending', 'cancelled', 'completed', 'failed')"
            ),
            nullable=False,
        ),
        sa.Column("completed_at", sa.Text(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
    )
    op.create_index("idx_deletion_audit_user", "deletion_audit", ["user_id"])
    op.create_index(
        "idx_deletion_audit_pending",
        "deletion_audit",
        ["status", "scheduled_for"],
        sqlite_where=sa.text("status = 'pending'"),
    )


def downgrade() -> None:
    op.drop_index("idx_deletion_audit_pending", table_name="deletion_audit")
    op.drop_index("idx_deletion_audit_user", table_name="deletion_audit")
    op.drop_table("deletion_audit")
    op.drop_index(
        "idx_account_deletion_cancel_user",
        table_name="account_deletion_cancel_tokens",
    )
    op.drop_table("account_deletion_cancel_tokens")
    op.drop_index("idx_users_scheduled_deletion", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("deletion_requested_at")
        batch.drop_column("scheduled_deletion_at")

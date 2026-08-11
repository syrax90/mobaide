"""Tests for the ``mode='immediate'`` branch of self-service deletion.

The legacy 14-day-grace path is covered by ``test_account_deletion.py``;
this file isolates the new ``_execute_immediate_deletion`` helper plus
the ``mode`` field threaded through ``request_account_deletion``. The
service contract for immediate mode is:

  1. seed-admin still rejected (mirrors scheduled mode)
  2. audit row written BEFORE any destructive step, with ``mode='immediate'``
     and ``scheduled_for == requested_at``
  3. refresh tokens revoked + WS-4010 closed before ``delete_user``
  4. ``delete_user`` cascades; audit row stamped ``completed``
  5. final-purge email dispatched (best-effort)
  6. NO ``scheduled_deletion_at`` written, NO cancel token created
  7. ``request_account_deletion`` returns ``None`` (vs ISO timestamp for
     scheduled)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from central_server.auth.service import (
    AccountDeletionAuthError,
    AccountDeletionForbiddenError,
    AuthService,
)
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from tests.conftest import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_JWT_SECRET,
)


class _FakeEmailSender:
    def __init__(self) -> None:
        self.requested: list[tuple[str, str, int, str]] = []
        self.completed: list[str] = []

    async def send_account_deletion_requested(
        self, to: str, scheduled_for_iso: str, grace_days: int, cancel_url: str,
    ) -> None:
        self.requested.append((to, scheduled_for_iso, grace_days, cancel_url))

    async def send_account_deletion_completed(self, to: str) -> None:
        self.completed.append(to)

    async def send_verification_email(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def send_password_reset_email(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def send_password_changed_notification(
        self, *args: Any, **kwargs: Any,
    ) -> None:
        pass


class _RoutingTracker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.disconnect_user_calls = 0
        self.disconnect_agent_calls = 0

    async def disconnect_user(self, user_id: str, code: int, reason: str) -> int:
        self.calls.append(("disconnect_user", user_id))
        self.disconnect_user_calls += 1
        return 0

    async def disconnect_agent(self, agent_id: str, code: int, reason: str) -> int:
        self.calls.append(("disconnect_agent", agent_id))
        self.disconnect_agent_calls += 1
        return 0


@pytest.fixture
def deletion_config(tmp_path) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        admin_seed_email=TEST_ADMIN_EMAIL,
        admin_seed_password=TEST_ADMIN_PASSWORD,
        db_path=str(tmp_path / "deletion.db"),
        log_level="DEBUG",
        log_format="console",
        email_verification_required=False,
        account_deletion_grace_days=14,
        account_deletion_cancel_token_ttl_hours=24,
    )


@pytest.fixture
async def deletion_db(deletion_config) -> Database:
    db = Database(deletion_config.db_path)
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
def fake_email() -> _FakeEmailSender:
    return _FakeEmailSender()


@pytest.fixture
def routing_tracker() -> _RoutingTracker:
    return _RoutingTracker()


@pytest.fixture
def deletion_service(
    deletion_config, deletion_db, fake_email, routing_tracker,
) -> AuthService:
    return AuthService(
        deletion_config,
        deletion_db,
        email_sender=fake_email,  # type: ignore[arg-type]
        routing=routing_tracker,  # type: ignore[arg-type]
    )


async def _make_password_user(
    db: Database, svc: AuthService, email: str, password: str,
) -> dict:
    pw_hash = await svc._hash_password(password)  # noqa: SLF001
    user_id = await db.create_user(
        email=email, password_hash=pw_hash, role="viewer", email_verified=True,
    )
    user = await db.get_user_by_id(user_id)
    assert user is not None
    return user


def _build_payload(user: dict):
    from central_server.auth.models import UserTokenPayload

    return UserTokenPayload(
        sub=user["id"],
        email=user["email"],
        role=user["role"],
        token_type="dashboard",
        exp=None,
        iat=0,
    )


class TestImmediateDeletionHappyPath:
    async def test_returns_none_and_cascades_user_row(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """Immediate mode returns ``None`` (no scheduled timestamp) and the
        user row is gone after the call."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "now@example.com", "pw",
        )
        proof = DeleteAccountRequest(
            method="password", password="pw", mode="immediate",
        )

        result = await deletion_service.request_account_deletion(
            _build_payload(user), source="android", proof=proof,
        )

        assert result is None
        assert await deletion_db.get_user_by_id(user["id"]) is None

    async def test_writes_audit_row_with_mode_immediate(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """Audit row outlives the user (Art. 17 forensic trail) and must
        carry ``mode='immediate'`` plus ``status='completed'`` after the
        cascade. ``scheduled_for`` must equal ``requested_at`` since there
        is no grace window."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "audit@example.com", "pw",
        )
        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(
                method="password", password="pw", mode="immediate",
            ),
        )

        cur = await deletion_db.conn.execute(
            "SELECT mode, status, source, email_at_request, "
            "requested_at, scheduled_for FROM deletion_audit "
            "WHERE user_id = ?",
            (user["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["mode"] == "immediate"
        assert row["status"] == "completed"
        assert row["source"] == "android"
        assert row["email_at_request"] == "audit@example.com"
        assert row["requested_at"] == row["scheduled_for"]

    async def test_does_not_create_cancel_token_or_scheduled_deletion_at(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """Immediate purge MUST NOT issue a cancel token nor stamp
        ``scheduled_deletion_at`` — the user row is gone before either
        could become observable, but we double-check by reading the
        cancel-token table directly."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "notoken@example.com", "pw",
        )
        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(
                method="password", password="pw", mode="immediate",
            ),
        )

        cur = await deletion_db.conn.execute(
            "SELECT COUNT(*) AS n FROM account_deletion_cancel_tokens "
            "WHERE user_id = ?",
            (user["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["n"] == 0

    async def test_dispatches_completion_email_only(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
        fake_email: _FakeEmailSender,
    ) -> None:
        """Immediate mode dispatches the final-purge completion email
        (best-effort, fire-and-forget). It must NOT send the
        "deletion requested" email — that one carries a cancel URL."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "completed@example.com", "pw",
        )
        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(
                method="password", password="pw", mode="immediate",
            ),
        )

        import asyncio
        await asyncio.sleep(0)

        assert fake_email.requested == []
        assert fake_email.completed == ["completed@example.com"]

    async def test_closes_websockets_before_user_row_teardown(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
        routing_tracker: _RoutingTracker,
    ) -> None:
        """WS-4010 must close BEFORE ``delete_user`` so a racing reconnect
        sees the row gone (not just scheduled). We assert routing was
        invoked for both the user and every owned agent."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "ws@example.com", "pw",
        )
        agent_a_id, _ = await deletion_db.register_agent("a-1", user["id"])
        agent_b_id, _ = await deletion_db.register_agent("a-2", user["id"])

        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(
                method="password", password="pw", mode="immediate",
            ),
        )

        assert routing_tracker.disconnect_user_calls == 1
        assert routing_tracker.disconnect_agent_calls == 2
        agent_targets = {
            c[1] for c in routing_tracker.calls if c[0] == "disconnect_agent"
        }
        assert agent_targets == {agent_a_id, agent_b_id}

    async def test_revokes_refresh_tokens_before_purge(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """Refresh tokens are wiped as part of the purge — any in-flight
        refresh from a backgrounded mobile app must fail closed."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "refresh@example.com", "pw",
        )
        agent_id, _ = await deletion_db.register_agent("agent-r", user["id"])
        await deletion_db.store_refresh_token(
            "r-imm-1", "dev-imm", agent_id, 60,
        )

        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(
                method="password", password="pw", mode="immediate",
            ),
        )

        assert await deletion_db.validate_and_consume_refresh("r-imm-1") is None

    async def test_purge_drops_push_registrations_with_the_device_rows(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """The immediate path deliberately does NOT call
        ``clear_push_tokens_for_user`` — the cascade deletes the
        ``mobile_devices`` rows outright, so a clear would have no observable
        effect."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "push@example.com", "pw",
        )
        await deletion_db.upsert_mobile_device(
            device_id="dev-imm", user_id=user["id"], name="iPhone", platform="ios",
        )
        await deletion_db.set_push_token(
            "dev-imm", user["id"], "ios", "tok-imm", "sandbox",
        )
        assert len(await deletion_db.list_push_targets(user["id"])) == 1

        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(
                method="password", password="pw", mode="immediate",
            ),
        )

        assert await deletion_db.list_push_targets(user["id"]) == []
        cur = await deletion_db.conn.execute(
            "SELECT COUNT(*) AS n FROM mobile_devices WHERE user_id = ?",
            (user["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["n"] == 0


class TestImmediateDeletionGuards:
    async def test_seed_admin_rejected_even_with_immediate_mode(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
        deletion_config: CentralServerConfig,
    ) -> None:
        """The seed-admin guard fires BEFORE the mode branch. Asking for
        immediate purge on the admin account must still be refused, and
        no audit row should land."""
        from central_server.auth.models import DeleteAccountRequest

        admin = await _make_password_user(
            deletion_db,
            deletion_service,
            deletion_config.admin_seed_email or "admin@x",
            "pw",
        )

        with pytest.raises(AccountDeletionForbiddenError):
            await deletion_service.request_account_deletion(
                _build_payload(admin),
                source="android",
                proof=DeleteAccountRequest(
                    method="password", password="pw", mode="immediate",
                ),
            )

        assert await deletion_db.get_user_by_id(admin["id"]) is not None
        cur = await deletion_db.conn.execute(
            "SELECT COUNT(*) AS n FROM deletion_audit WHERE user_id = ?",
            (admin["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["n"] == 0

    async def test_wrong_password_blocks_immediate_purge(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """``_verify_fresh_credentials`` runs before the mode branch. A
        bad password on immediate mode must NOT cascade the user."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "bad@example.com", "right",
        )

        with pytest.raises(AccountDeletionAuthError):
            await deletion_service.request_account_deletion(
                _build_payload(user),
                source="android",
                proof=DeleteAccountRequest(
                    method="password", password="WRONG", mode="immediate",
                ),
            )

        assert await deletion_db.get_user_by_id(user["id"]) is not None
        cur = await deletion_db.conn.execute(
            "SELECT COUNT(*) AS n FROM deletion_audit WHERE user_id = ?",
            (user["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["n"] == 0


class TestScheduledModeStillDefault:
    async def test_default_mode_is_scheduled_when_omitted(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """Older clients omit ``mode`` entirely. The pydantic default must
        keep them on the scheduled path so the API stays
        backward-compatible."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "legacy@example.com", "pw",
        )

        # NB: mode field omitted — pydantic fills it with "scheduled".
        scheduled_iso = await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(method="password", password="pw"),
        )

        # Scheduled path → returns ISO timestamp, not None.
        assert isinstance(scheduled_iso, str)
        # ~14 days out per default grace window.
        delta = (
            datetime.fromisoformat(scheduled_iso)
            - datetime.now(timezone.utc)
        )
        assert timedelta(days=13, hours=23) <= delta <= timedelta(days=14, hours=1)

        # User row still present in soft-delete state.
        refreshed = await deletion_db.get_user_by_id(user["id"])
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] == scheduled_iso

        # Audit row carries mode='scheduled'.
        cur = await deletion_db.conn.execute(
            "SELECT mode FROM deletion_audit WHERE user_id = ?",
            (user["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["mode"] == "scheduled"

    async def test_explicit_scheduled_mode_writes_scheduled_audit(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """Explicit ``mode='scheduled'`` matches the default. Verifies the
        audit row stores the literal client-supplied value (no silent
        coercion to immediate)."""
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "explicit@example.com", "pw",
        )
        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(
                method="password", password="pw", mode="scheduled",
            ),
        )

        cur = await deletion_db.conn.execute(
            "SELECT mode, status FROM deletion_audit WHERE user_id = ?",
            (user["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["mode"] == "scheduled"
        assert row["status"] == "pending"

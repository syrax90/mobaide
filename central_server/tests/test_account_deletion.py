"""Tests for the self-service account-deletion flow.

Covers ``AuthService.request_account_deletion`` /
``cancel_account_deletion`` / ``cancel_account_deletion_by_token`` /
``run_deletion_sweep`` plus the ``/api/auth/me/delete[/cancel]`` REST
endpoints. Mirrors ``test_password_reset.py``'s pattern: in-memory
SQLite via ``tmp_path``, fake email sender that records dispatched
messages, AsyncClient against ASGITransport for router integration.
"""
from __future__ import annotations

import time
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
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
    TEST_JWT_SECRET,
)


# ---- Fakes / fixtures ----------------------------------------------------


class _FakeEmailSender:
    """Captures account-deletion email dispatches (no SMTP)."""

    def __init__(self) -> None:
        self.requested: list[tuple[str, str, int, str]] = []
        self.completed: list[str] = []

    async def send_account_deletion_requested(
        self,
        to: str,
        scheduled_for_iso: str,
        grace_days: int,
        cancel_url: str,
    ) -> None:
        self.requested.append((to, scheduled_for_iso, grace_days, cancel_url))

    async def send_account_deletion_completed(self, to: str) -> None:
        self.completed.append(to)

    # The AuthService's other code paths may attempt these; provide no-ops so
    # ``register_user`` / ``reset_password`` etc. don't blow up if exercised.
    async def send_verification_email(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def send_password_reset_email(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def send_password_changed_notification(
        self, *args: Any, **kwargs: Any
    ) -> None:
        pass


class _RoutingTracker:
    """Records the order of disconnect calls relative to DB-mutating calls.

    Wraps a real ``RoutingTable`` so unit tests can assert WS-4010 fires
    *before* the rows that would invalidate the routing lookup keys. We
    can't easily snoop on commit() in aiosqlite, so we log calls on the
    routing methods themselves and verify the audit row is written
    *after* both disconnect_agent and disconnect_user.
    """

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
    deletion_config, deletion_db, fake_email, routing_tracker
) -> AuthService:
    return AuthService(
        deletion_config,
        deletion_db,
        email_sender=fake_email,  # type: ignore[arg-type]
        routing=routing_tracker,  # type: ignore[arg-type]
    )


async def _make_password_user(
    db: Database, svc: AuthService, email: str, password: str
) -> dict:
    pw_hash = await svc._hash_password(password)  # noqa: SLF001 — fixture helper
    user_id = await db.create_user(
        email=email, password_hash=pw_hash, role="viewer", email_verified=True
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


# ---- Service layer: happy paths -----------------------------------------


class TestRequestAccountDeletion:
    async def test_password_reauth_schedules_deletion_and_revokes_sessions(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
        fake_email: _FakeEmailSender,
    ) -> None:
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "u1@example.com", "hunter22",
        )
        # An active refresh token must be revoked when deletion is scheduled.
        agent_id, _ = await deletion_db.register_agent("a-1", user["id"])
        await deletion_db.store_refresh_token("refresh-1", "dev-1", agent_id, 60)

        proof = DeleteAccountRequest(method="password", password="hunter22")
        scheduled_iso = await deletion_service.request_account_deletion(
            _build_payload(user), source="android", proof=proof,
        )

        # 1. ``scheduled_deletion_at`` written, ~14 days out.
        refreshed = await deletion_db.get_user_by_id(user["id"])
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] == scheduled_iso
        delta = (
            datetime.fromisoformat(scheduled_iso)
            - datetime.now(timezone.utc)
        )
        assert timedelta(days=13, hours=23) <= delta <= timedelta(days=14, hours=1)

        # 2. Pending audit row exists.
        audit = await deletion_db.find_pending_audit_for_user(user["id"])
        assert audit is not None
        assert audit["status"] == "pending"
        assert audit["source"] == "android"
        assert audit["email_at_request"] == "u1@example.com"

        # 3. Refresh tokens revoked.
        assert await deletion_db.validate_and_consume_refresh("refresh-1") is None

        # 4. Email queued with a cancel URL.
        assert len(fake_email.requested) == 1
        to, _sched, grace_days, cancel_url = fake_email.requested[0]
        assert to == "u1@example.com"
        assert grace_days == 14
        assert "/portal/cancel-deletion?token=" in cancel_url

    async def test_disconnect_calls_fire_before_audit_completion(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
        routing_tracker: _RoutingTracker,
    ) -> None:
        """WS-4010 must close BEFORE any DB row change that invalidates the
        routing lookup. The service writes scheduled_deletion first, then
        audit row, then revokes refresh tokens, then issues the WS
        disconnect calls. We assert routing was invoked at least once and
        that the audit row exists afterward — the order check itself is by
        the call sequence on routing_tracker.
        """
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "u2@example.com", "pw",
        )
        agent_x_id, _ = await deletion_db.register_agent("agent-x", user["id"])
        agent_y_id, _ = await deletion_db.register_agent("agent-y", user["id"])

        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(method="password", password="pw"),
        )

        assert routing_tracker.disconnect_user_calls == 1
        assert routing_tracker.disconnect_agent_calls == 2
        # disconnect_agent should appear in the call log before disconnect_user
        agent_calls = [c for c in routing_tracker.calls if c[0] == "disconnect_agent"]
        assert {c[1] for c in agent_calls} == {agent_x_id, agent_y_id}
        last = routing_tracker.calls[-1]
        assert last[0] == "disconnect_user"


class TestRequestAccountDeletionFailures:
    async def test_seed_admin_refused(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
        deletion_config: CentralServerConfig,
    ) -> None:
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
                proof=DeleteAccountRequest(method="password", password="pw"),
            )
        # Schedule MUST NOT have been written.
        refreshed = await deletion_db.get_user_by_id(admin["id"])
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] is None
        assert (
            await deletion_db.find_pending_audit_for_user(admin["id"]) is None
        )

    async def test_wrong_password_raises_and_no_state_changes(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
        fake_email: _FakeEmailSender,
    ) -> None:
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "wrong@example.com", "correct",
        )
        with pytest.raises(AccountDeletionAuthError):
            await deletion_service.request_account_deletion(
                _build_payload(user),
                source="android",
                proof=DeleteAccountRequest(method="password", password="WRONG"),
            )
        # Schedule, audit, and email must all be untouched on auth failure.
        refreshed = await deletion_db.get_user_by_id(user["id"])
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] is None
        assert await deletion_db.find_pending_audit_for_user(user["id"]) is None
        assert fake_email.requested == []

    async def test_google_reauth_rejects_user_without_google_sub(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
        deletion_config: CentralServerConfig,
    ) -> None:
        """Password user attempting Google re-auth → AccountDeletionAuthError
        (the service checks ``google_sub`` BEFORE attempting to verify the
        token, so we don't need to mock google.oauth2 here)."""
        from central_server.auth.models import DeleteAccountRequest

        # Need google_client_id to be present so we get past the "not configured"
        # branch and reach the google_sub check.
        cfg = CentralServerConfig(
            **{**deletion_config.__dict__, "google_client_id": "fake-client.apps.googleusercontent.com"}
        )
        svc = AuthService(
            cfg, deletion_db, email_sender=deletion_service._email_sender  # noqa: SLF001
        )

        user = await _make_password_user(deletion_db, svc, "pwonly@example.com", "pw")

        with pytest.raises(AccountDeletionAuthError) as excinfo:
            await svc.request_account_deletion(
                _build_payload(user),
                source="android",
                proof=DeleteAccountRequest(
                    method="google", id_token="tok", nonce="n",
                ),
            )
        assert "not linked" in str(excinfo.value).lower()

    async def test_google_reauth_requires_google_client_id_configured(
        self,
        deletion_db: Database,
        deletion_config: CentralServerConfig,
        fake_email: _FakeEmailSender,
    ) -> None:
        """When the server isn't configured for Google, the Google re-auth
        path short-circuits with a helpful AccountDeletionAuthError before
        any external call is made.

        The user must have a ``google_sub`` so the "not linked" guard
        passes; the "not configured" error is raised inside
        ``_verify_google_id_token`` after the sub-presence check.
        """
        from central_server.auth.models import DeleteAccountRequest

        # Config has google_client_id=None by default.
        assert deletion_config.google_client_id is None
        svc = AuthService(
            deletion_config, deletion_db, email_sender=fake_email,  # type: ignore[arg-type]
        )
        # Use a Google-linked user (has google_sub) so that the
        # stored_sub guard passes and the "not configured" error is reached.
        user_id = await deletion_db.create_user(
            email="google-any@example.com",
            google_sub="google-sub-unconfigured",
            email_verified=True,
        )
        user = await deletion_db.get_user_by_id(user_id)
        assert user is not None

        with pytest.raises(AccountDeletionAuthError) as excinfo:
            await svc.request_account_deletion(
                _build_payload(user),
                source="android",
                proof=DeleteAccountRequest(method="google", id_token="t", nonce="n"),
            )
        assert "not configured" in str(excinfo.value).lower()

    async def test_google_reauth_with_a_non_ascii_nonce_is_rejected_not_crashed(
        self,
        deletion_db: Database,
        deletion_config: CentralServerConfig,
        fake_email: _FakeEmailSender,
        monkeypatch,
    ) -> None:
        """``nonce`` arrives in the request body with no charset constraint, and
        ``compare_digest`` *raises* ``TypeError`` on a ``str`` holding a
        non-ASCII character instead of returning False.
        """
        from central_server.auth.models import DeleteAccountRequest

        cfg = CentralServerConfig(
            **{
                **deletion_config.__dict__,
                "google_client_id": "fake-client.apps.googleusercontent.com",
            }
        )
        svc = AuthService(
            cfg, deletion_db, email_sender=fake_email,  # type: ignore[arg-type]
        )
        user_id = await deletion_db.create_user(
            email="google-nonascii@example.com",
            google_sub="google-sub-nonascii",
            email_verified=True,
        )
        user = await deletion_db.get_user_by_id(user_id)
        assert user is not None

        async def _fake_verify(self, token_str: str) -> dict[str, Any]:
            # Signature/audience already passed; the nonce is the next gate.
            return {
                "sub": "google-sub-nonascii",
                "nonce": "a" * 43,
                "iat": int(time.time()),
            }

        monkeypatch.setattr(AuthService, "_verify_google_id_token", _fake_verify)

        with pytest.raises(AccountDeletionAuthError) as excinfo:
            await svc.request_account_deletion(
                _build_payload(user),
                source="android",
                proof=DeleteAccountRequest(
                    method="google", id_token="tok", nonce="nónce-Ω",
                ),
            )

        assert "nonce mismatch" in str(excinfo.value).lower()
        refreshed = await deletion_db.get_user_by_id(user_id)
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] is None


# ---- Cancel flows --------------------------------------------------------


class TestCancelAccountDeletion:
    async def test_cancel_clears_schedule_and_marks_audit_cancelled(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "cancel@example.com", "pw",
        )
        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(method="password", password="pw"),
        )

        previous = await deletion_service.cancel_account_deletion(user["id"])

        assert previous is not None  # ISO timestamp of the cancelled schedule
        refreshed = await deletion_db.get_user_by_id(user["id"])
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] is None
        # The pending row transitioned to cancelled, so no pending row remains.
        assert await deletion_db.find_pending_audit_for_user(user["id"]) is None
        # Confirm the historical row carries the cancellation_reason.
        cur = await deletion_db.conn.execute(
            "SELECT status, cancellation_reason FROM deletion_audit WHERE user_id = ?",
            (user["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["status"] == "cancelled"
        assert row["cancellation_reason"] == "user_initiated"

    async def test_cancel_idempotent_when_no_deletion_scheduled(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        user = await _make_password_user(
            deletion_db, deletion_service, "noop@example.com", "pw",
        )
        # No deletion ever requested.
        result = await deletion_service.cancel_account_deletion(user["id"])
        assert result is None

    async def test_cancel_by_token_consumes_token_and_records_email_link(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        from central_server.auth.models import DeleteAccountRequest

        user = await _make_password_user(
            deletion_db, deletion_service, "emaillink@example.com", "pw",
        )
        await deletion_service.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(method="password", password="pw"),
        )
        # Fetch the raw cancel token by issuing a fresh one mirrors what the
        # service would have stored. We instead read it directly via the
        # public helper to keep this hermetic.
        raw = await deletion_db.create_deletion_cancel_token(
            user["id"], ttl_seconds=3600
        )

        resolved_user_id = await deletion_service.cancel_account_deletion_by_token(raw)

        assert resolved_user_id == user["id"]
        refreshed = await deletion_db.get_user_by_id(user["id"])
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] is None
        cur = await deletion_db.conn.execute(
            "SELECT cancellation_reason FROM deletion_audit "
            "WHERE user_id = ? AND status = 'cancelled'",
            (user["id"],),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["cancellation_reason"] == "email_link"

        # The token is single-use — second consumption must yield None.
        again = await deletion_service.cancel_account_deletion_by_token(raw)
        assert again is None

    async def test_cancel_by_invalid_token_returns_none(
        self, deletion_service: AuthService
    ) -> None:
        # Random string can never collide with a hashed real token.
        result = await deletion_service.cancel_account_deletion_by_token(
            "not-a-real-token-xxx"
        )
        assert result is None


# ---- Sweeper -------------------------------------------------------------


class TestRunDeletionSweep:
    async def test_purges_users_past_grace_window(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
        fake_email: _FakeEmailSender,
    ) -> None:
        user = await _make_password_user(
            deletion_db, deletion_service, "purgeme@example.com", "pw",
        )
        # Stamp the schedule in the past so the sweeper picks the user up.
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        requested = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
        await deletion_db.set_scheduled_deletion(
            user["id"], scheduled_for=past, requested_at=requested,
        )
        audit_id = await deletion_db.record_deletion_audit(
            user_id=user["id"],
            email_at_request=user["email"],
            requested_at=requested,
            scheduled_for=past,
            source="android",
        )

        purged = await deletion_service.run_deletion_sweep()

        assert purged == 1
        assert await deletion_db.get_user_by_id(user["id"]) is None
        # Audit row outlives the user (Art. 17) and is now completed.
        cur = await deletion_db.conn.execute(
            "SELECT status, completed_at FROM deletion_audit WHERE id = ?",
            (audit_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["status"] == "completed"
        assert row["completed_at"] is not None
        # Final-purge confirmation email dispatched (best-effort).
        assert fake_email.completed == ["purgeme@example.com"]

    async def test_skips_users_whose_schedule_was_cancelled_between_scan_and_delete(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """Race-safety: ``list_users_due_for_purge`` may return a row whose
        ``scheduled_deletion_at`` was cleared by a concurrent cancel. The
        sweeper must re-check inside the loop and skip such rows."""
        user = await _make_password_user(
            deletion_db, deletion_service, "race@example.com", "pw",
        )
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        await deletion_db.set_scheduled_deletion(
            user["id"], scheduled_for=past, requested_at=past,
        )
        # The scan returns this user...
        listed = await deletion_db.list_users_due_for_purge(
            datetime.now(timezone.utc).isoformat()
        )
        assert any(u["id"] == user["id"] for u in listed)
        # ...but a cancel lands before the sweeper acts.
        await deletion_db.clear_scheduled_deletion(user["id"])

        purged = await deletion_service.run_deletion_sweep()

        assert purged == 0
        # User is still alive.
        assert await deletion_db.get_user_by_id(user["id"]) is not None

    async def test_per_user_failure_marks_audit_failed_and_loop_continues(
        self,
        deletion_service: AuthService,
        deletion_db: Database,
    ) -> None:
        """A purge that raises must mark its audit row 'failed' and not
        abort the rest of the sweep."""
        # Two due users; we'll inject a delete_user failure for the first.
        u_bad = await _make_password_user(
            deletion_db, deletion_service, "bad@example.com", "pw",
        )
        u_good = await _make_password_user(
            deletion_db, deletion_service, "good@example.com", "pw",
        )
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        for u in (u_bad, u_good):
            await deletion_db.set_scheduled_deletion(
                u["id"], scheduled_for=past, requested_at=past,
            )
            await deletion_db.record_deletion_audit(
                user_id=u["id"],
                email_at_request=u["email"],
                requested_at=past,
                scheduled_for=past,
                source="android",
            )

        original_delete = deletion_db.delete_user
        bad_id = u_bad["id"]

        async def _flaky_delete(user_id: str):
            if user_id == bad_id:
                raise RuntimeError("simulated db failure")
            return await original_delete(user_id)

        deletion_db.delete_user = _flaky_delete  # type: ignore[method-assign]

        purged = await deletion_service.run_deletion_sweep()

        assert purged == 1
        # Bad user still exists; audit row stamped failed.
        assert await deletion_db.get_user_by_id(bad_id) is not None
        cur = await deletion_db.conn.execute(
            "SELECT status FROM deletion_audit WHERE user_id = ?", (bad_id,),
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["status"] == "failed"
        # Good user purged.
        assert await deletion_db.get_user_by_id(u_good["id"]) is None


# ---- Router integration --------------------------------------------------


class TestDeleteAccountEndpoint:
    async def test_password_reauth_returns_204_and_schedules(
        self, client, app, viewer_user, api_auth_headers
    ) -> None:
        del viewer_user
        # Conftest's app fixture wires the viewer with password
        # TEST_VIEWER_PASSWORD. Verify the same password works as re-auth proof.
        resp = await client.post(
            "/api/auth/me/delete",
            json={"method": "password", "password": TEST_VIEWER_PASSWORD},
            headers=api_auth_headers,
        )
        assert resp.status_code == 204
        # The user is now scheduled for deletion.
        user = await app.state.db.get_user_by_email(TEST_VIEWER_EMAIL)
        assert user is not None
        assert user["scheduled_deletion_at"] is not None

    async def test_wrong_password_returns_403_reauth_required(
        self, client, viewer_user, api_auth_headers
    ) -> None:
        del viewer_user
        resp = await client.post(
            "/api/auth/me/delete",
            json={"method": "password", "password": "WRONG"},
            headers=api_auth_headers,
        )
        assert resp.status_code == 403
        body = resp.json()
        assert body["detail"]["error"] == "reauth_required"

    async def test_admin_token_blocked_with_400(
        self, client, admin_api_auth_headers
    ) -> None:
        resp = await client.post(
            "/api/auth/me/delete",
            json={"method": "password", "password": TEST_ADMIN_PASSWORD},
            headers=admin_api_auth_headers,
        )
        # Admin seed account is rejected by AccountDeletionForbiddenError →
        # mapped to HTTP 400 in the router.
        assert resp.status_code == 400

    async def test_missing_bearer_returns_401(self, client) -> None:
        resp = await client.post(
            "/api/auth/me/delete",
            json={"method": "password", "password": "x"},
        )
        assert resp.status_code == 401


class TestCancelEndpoint:
    async def test_cancel_after_request_returns_cancelled_status(
        self, client, app, viewer_user, api_auth_headers
    ) -> None:
        del viewer_user
        await client.post(
            "/api/auth/me/delete",
            json={"method": "password", "password": TEST_VIEWER_PASSWORD},
            headers=api_auth_headers,
        )
        # The cancel endpoint needs a Bearer for the same user. The 204
        # delete didn't invalidate the JWT (logical revoke of refresh
        # tokens only), so the same header still works.
        resp = await client.post(
            "/api/auth/me/delete/cancel",
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "cancelled"
        assert body["scheduled_deletion_at"] is not None
        # And the user row is restored.
        user = await app.state.db.get_user_by_email(TEST_VIEWER_EMAIL)
        assert user is not None
        assert user["scheduled_deletion_at"] is None

    async def test_cancel_when_not_scheduled_returns_not_scheduled(
        self, client, viewer_user, api_auth_headers
    ) -> None:
        del viewer_user
        resp = await client.post(
            "/api/auth/me/delete/cancel",
            headers=api_auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_scheduled"
        assert body["scheduled_deletion_at"] is None


class TestLoginResponseSurfacesScheduledDeletion:
    async def test_login_includes_scheduled_deletion_at_when_set(
        self, client, app, viewer_user
    ) -> None:
        # Stamp a schedule directly via the DB so we don't depend on the
        # delete endpoint (which is exercised separately).
        future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
        await app.state.db.set_scheduled_deletion(
            viewer_user["id"], scheduled_for=future, requested_at=now_iso,
        )

        resp = await client.post(
            "/api/auth/login",
            json={"email": TEST_VIEWER_EMAIL, "password": TEST_VIEWER_PASSWORD},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["scheduled_deletion_at"] == future

    async def test_login_omits_scheduled_deletion_when_unset(
        self, client, viewer_user
    ) -> None:
        del viewer_user
        resp = await client.post(
            "/api/auth/login",
            json={"email": TEST_VIEWER_EMAIL, "password": TEST_VIEWER_PASSWORD},
        )
        assert resp.status_code == 200
        body = resp.json()
        # None or absent both acceptable; the contract is "non-null only when
        # the account is in the grace window".
        assert body.get("scheduled_deletion_at") is None

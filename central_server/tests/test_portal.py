"""Tests for the end-user web portal.

Covers the new ``/portal/*`` routes that are siblings of the admin
dashboard: cookie isolation (``portal_token`` ≠ ``dashboard_token``),
public reachability of the Play-Console URL, owned-agent isolation on
``/portal/account``, CSRF enforcement on state-changing POSTs, and the
public ``/portal/cancel-deletion`` token-consume path.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.conftest import (
    TEST_VIEWER_EMAIL,
    TEST_VIEWER_PASSWORD,
)


# ---- Helpers -------------------------------------------------------------


async def _portal_login_cookie(app, email: str, password: str) -> str:
    """Issue a fresh portal JWT through the service for a registered user.

    Bypasses ``POST /portal/login`` to keep these tests narrow — the login
    form is exercised by its own test below.
    """
    auth_service = app.state.auth_service
    _uid, _role, token, _scheduled = await auth_service.login_user(email, password)
    return token


async def _grab_csrf(client) -> tuple[str, dict[str, str]]:
    """Hit a portal GET to harvest the CSRF cookie + matching token.

    Returns ``(token, cookies_dict)``. The cookie jar attached to the
    AsyncClient already carries the cookie for subsequent calls, so the
    second return value is mostly for documentation in tests that mix
    multiple clients. The token itself is parsed out of the ``Set-Cookie``
    header on the GET response because the template-embedded token is HTML.
    """
    resp = await client.get("/portal/login")
    assert resp.status_code == 200
    cookie_header = resp.headers.get("set-cookie", "")
    # ``portal_csrf=<nonce>.<digest>; Path=/; ...`` — extract the value.
    token = ""
    for piece in cookie_header.split(","):
        piece = piece.strip()
        if piece.startswith("portal_csrf="):
            token = piece.split(";", 1)[0].removeprefix("portal_csrf=")
            break
    assert token, f"portal_csrf cookie not set; saw: {cookie_header!r}"
    return token, {"portal_csrf": token}


# ---- Public, no-auth pages ----------------------------------------------


class TestPublicPortalPages:
    async def test_delete_account_info_page_reachable_without_auth(
        self, client
    ) -> None:
        """The URL Google Play Console points to MUST work without
        authentication — that is the whole point of the public
        ``delete-account-info`` route (plan + Data Safety form)."""
        resp = await client.get("/portal/delete-account-info")
        assert resp.status_code == 200
        # No redirect to login.
        assert resp.url.path == "/portal/delete-account-info"

    async def test_cancel_deletion_get_with_no_token_renders_missing_token(
        self, client
    ) -> None:
        resp = await client.get("/portal/cancel-deletion")
        # The GET path is also public — it renders a confirmation form
        # before consuming the token (avoids accidental link-scanner hits).
        assert resp.status_code == 200

    async def test_login_page_reachable_without_auth(self, client) -> None:
        resp = await client.get("/portal/login")
        assert resp.status_code == 200


# ---- Cookie namespace isolation -----------------------------------------


class TestPortalCookieIsolation:
    async def test_portal_cookie_does_not_unlock_dashboard_routes(
        self, client, app, viewer_user
    ) -> None:
        """A non-admin signing into the portal receives a ``portal_token``
        cookie. That cookie must NOT pass ``get_dashboard_user`` for the
        admin dashboard — admin routes look for ``dashboard_token`` only.
        """
        del viewer_user
        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )
        resp = await client.get(
            "/dashboard",
            headers={"Cookie": f"portal_token={token}"},
        )
        # No dashboard cookie present → 303 redirect to /dashboard/login.
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/dashboard/login")

    async def test_dashboard_cookie_does_not_unlock_portal_account(
        self, client, dashboard_headers
    ) -> None:
        """Symmetric: an admin presenting a ``dashboard_token`` against the
        portal must be redirected to the portal login. ``get_portal_user``
        reads ``portal_token`` only.
        """
        resp = await client.get("/portal/account", headers=dashboard_headers)
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/portal/login")

    async def test_portal_account_requires_portal_token_cookie(
        self, client
    ) -> None:
        """No cookies at all → redirect to /portal/login."""
        resp = await client.get("/portal/account")
        assert resp.status_code == 303
        assert resp.headers["location"].startswith("/portal/login")

    async def test_portal_token_unlocks_portal_account(
        self, client, app, viewer_user
    ) -> None:
        del viewer_user
        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )
        resp = await client.get(
            "/portal/account",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200


# ---- CSRF protection on state-changing routes ---------------------------


class TestPortalCsrf:
    async def test_cancel_deletion_post_without_csrf_returns_403(
        self, client
    ) -> None:
        """``/portal/cancel-deletion`` requires the double-submit CSRF
        pair. With no cookie+header, the dependency raises 403."""
        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "anything"},
        )
        assert resp.status_code == 403

    async def test_cancel_deletion_post_with_matching_pair_accepted(
        self, client
    ) -> None:
        """Cookie + matching header → CSRF check passes. The token itself
        is bogus (no DB row) so the route renders the "invalid_token"
        outcome with HTTP 200 — but that's downstream of CSRF: the only
        thing under test here is that we didn't 403 out.
        """
        token, _ = await _grab_csrf(client)
        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "bogus-cancel-token", "csrf_token": token},
            headers={"X-CSRF-Token": token},
        )
        # Important assertion: NOT 403. The route always returns 200 here
        # because the cancel-deletion HTML page renders the outcome.
        assert resp.status_code != 403
        assert resp.status_code == 200

    async def test_cancel_deletion_post_cookie_only_no_header_returns_403(
        self, client
    ) -> None:
        """Half of the double-submit pair (cookie only, no header / form
        field) must still be rejected. Confirms we can't get past CSRF by
        merely passing the cookie back."""
        # Seed the cookie jar by hitting any portal GET.
        await _grab_csrf(client)
        # Now POST without supplying X-CSRF-Token or csrf_token form field.
        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "anything"},
        )
        assert resp.status_code == 403


# ---- Owned-data isolation -----------------------------------------------


class TestPortalAccountIsolation:
    async def test_portal_account_lists_only_callers_own_agents(
        self, client, app, viewer_user
    ) -> None:
        """Every authenticated portal route filters by ``user.sub``. A
        viewer signed into the portal must see ONLY their own agents.
        """
        # Second user (NOT seed-admin, NOT viewer) with an agent of their own.
        await app.state.auth_service.register_user(
            "other@test.local", "otherpass123",
        )
        other = await app.state.db.get_user_by_email("other@test.local")
        assert other is not None
        _, _ = await app.state.db.register_agent("other-secret-agent", other["id"])

        # Viewer gets a clearly-named agent of their own.
        _, _ = await app.state.db.register_agent(
            "viewer-only-agent", viewer_user["id"],
        )

        token = await _portal_login_cookie(
            app, TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD,
        )
        resp = await client.get(
            "/portal/account",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200
        body = resp.text
        assert "viewer-only-agent" in body
        assert "other-secret-agent" not in body


# ---- Public cancel-by-email-token path ----------------------------------


class TestPublicCancelByToken:
    async def test_post_with_valid_token_clears_schedule(
        self, client, app, viewer_user
    ) -> None:
        """A real, unconsumed cancel token in a public POST cancels the
        scheduled deletion. CSRF is still enforced because the route is
        cookie-authed (CSRF cookie must accompany), but no portal_token
        is required — this is the email-link path."""
        future = (datetime.now(timezone.utc) + timedelta(days=14)).isoformat()
        now_iso = datetime.now(timezone.utc).isoformat()
        await app.state.db.set_scheduled_deletion(
            viewer_user["id"], scheduled_for=future, requested_at=now_iso,
        )
        await app.state.db.record_deletion_audit(
            user_id=viewer_user["id"],
            email_at_request=viewer_user["email"],
            requested_at=now_iso,
            scheduled_for=future,
            source="android",
        )
        raw_token = await app.state.db.create_deletion_cancel_token(
            viewer_user["id"], ttl_seconds=3600,
        )

        csrf_token, _ = await _grab_csrf(client)

        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": raw_token, "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
        )
        assert resp.status_code == 200
        # Server cleared the schedule.
        refreshed = await app.state.db.get_user_by_id(viewer_user["id"])
        assert refreshed is not None
        assert refreshed["scheduled_deletion_at"] is None

    async def test_post_with_invalid_token_returns_200_with_invalid_outcome(
        self, client
    ) -> None:
        csrf_token, _ = await _grab_csrf(client)
        resp = await client.post(
            "/portal/cancel-deletion",
            data={"token": "definitely-not-a-real-token", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
        )
        # Page renders the "invalid_token" outcome with 200; never leaks
        # whether the token shape was right but unknown vs malformed.
        assert resp.status_code == 200

"""The ``session_transport`` switch on the session-minting auth endpoints.

Every one of them funnels through ``_apply_refreshable_session``, so the two
things worth pinning are that the mobile (body) shape is byte-for-byte what it
was, and that a cookie-mode request which mints *nothing* still clears the jar
— mint or clear, never neither.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from central_server.auth.service import AuthService
from tests.conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD

REFRESH = "__Host-mobaide_refresh"

_MINTING = {
    "/api/auth/login": {
        "email": TEST_VIEWER_EMAIL,
        "password": TEST_VIEWER_PASSWORD,
    },
    "/api/auth/register": {
        "email": "cookie-mint@test.local",
        "password": "hunter2222",
    },
}


class _FakeEmailSender:
    """Swallows the OTP; no test here redeems one."""

    async def send_verification_email(
        self, to: str, code: str, ttl_minutes: int
    ) -> None:
        pass

    async def send_password_changed_notification(
        self, *args: Any, **kwargs: Any
    ) -> None:
        pass


class TestBodyTransportIsUnchanged:
    @pytest.mark.parametrize("transport", [None, "body"])
    async def test_the_token_stays_in_the_body_and_no_cookie_is_set(
        self, client, config, viewer_user, transport
    ) -> None:
        """Mobile omits the field entirely; ``"body"`` must mean the same."""
        del viewer_user
        body = {
            "email": TEST_VIEWER_EMAIL,
            "password": TEST_VIEWER_PASSWORD,
            "refreshable": True,
        }
        if transport is not None:
            body["session_transport"] = transport

        resp = await client.post("/api/auth/login", json=body)

        payload = resp.json()
        assert resp.status_code == 200
        assert payload["refresh_token"]
        assert payload["session_transport"] == "body"
        assert (
            payload["expires_in"]
            == config.user_session_access_expire_minutes * 60
        )
        assert resp.headers.get_list("set-cookie") == []


class TestCookieTransport:
    @pytest.mark.parametrize("path", sorted(_MINTING))
    async def test_every_minting_endpoint_routes_through_the_same_funnel(
        self, client, viewer_user, path
    ) -> None:
        del viewer_user
        resp = await client.post(
            path,
            json={
                **_MINTING[path],
                "refreshable": True,
                "session_transport": "cookie",
            },
        )

        payload = resp.json()
        assert resp.status_code == 200
        assert payload["access_token"]
        # The credential is in the jar; leaving a copy in the body would put a
        # 30-day token back inside reach of any script on the origin.
        assert payload["refresh_token"] is None
        assert payload["session_transport"] == "cookie"
        assert any(
            c.startswith(f"{REFRESH}=") and "Max-Age=0" not in c
            for c in resp.headers.get_list("set-cookie")
        )

    @pytest.mark.parametrize(
        "path,body",
        [
            ("/api/auth/google", {"id_token": "not-a-token"}),
            ("/api/auth/apple", {"id_token": "not-a-token", "nonce": "n"}),
            (
                "/api/auth/verify-email",
                {"email": TEST_VIEWER_EMAIL, "code": "000000"},
            ),
        ],
    )
    async def test_the_remaining_minting_models_accept_the_field(
        self, client, viewer_user, path, body
    ) -> None:
        """These three reject their credential deeper in the handler; a 422
        would mean the request model never grew the field."""
        del viewer_user
        resp = await client.post(
            path,
            json={**body, "refreshable": True, "session_transport": "cookie"},
        )

        assert resp.status_code != 422


class TestAMintThatProducesNothingClearsTheJar:
    @pytest.fixture
    async def verification_required(self, app, config, db):
        """Put the app on the OTP path without rebuilding it.

        ``CentralServerConfig`` is frozen, so the flag rides a replacement
        ``AuthService``. The router reads it and ``app.state.config``
        per request, so the cookie attributes keep coming from the real config.
        """
        previous = app.state.auth_service
        app.state.auth_service = AuthService(
            dataclasses.replace(config, email_verification_required=True),
            db,
            email_sender=_FakeEmailSender(),  # type: ignore[arg-type]
            routing=app.state.routing,
        )
        yield
        app.state.auth_service.close()
        app.state.auth_service = previous

    async def test_a_non_refreshable_cookie_request_leaves_no_credential(
        self, client, viewer_user
    ) -> None:
        del viewer_user
        resp = await client.post(
            "/api/auth/login",
            json={
                "email": TEST_VIEWER_EMAIL,
                "password": TEST_VIEWER_PASSWORD,
                "refreshable": False,
                "session_transport": "cookie",
            },
        )

        assert resp.status_code == 200
        assert client.cookies.get(REFRESH) is None

    async def test_verification_required_clears_the_cookie_already_in_the_jar(
        self, client, viewer_user, verification_required
    ) -> None:
        """Signing in as a second account over a live session must not leave
        the first account's credential behind for the next boot to restore."""
        del viewer_user, verification_required
        await client.post(
            "/api/auth/login",
            json={
                "email": TEST_VIEWER_EMAIL,
                "password": TEST_VIEWER_PASSWORD,
                "refreshable": True,
                "session_transport": "cookie",
            },
        )
        assert client.cookies.get(REFRESH) is not None

        resp = await client.post(
            "/api/auth/register",
            json={
                "email": "second-account@test.local",
                "password": "hunter2222",
                "refreshable": True,
                "session_transport": "cookie",
            },
        )

        payload = resp.json()
        assert resp.status_code == 200
        assert payload["status"] == "verification_required"
        assert payload["access_token"] is None
        assert any(
            c.startswith(f"{REFRESH}=") and "Max-Age=0" in c
            for c in resp.headers.get_list("set-cookie")
        )
        assert client.cookies.get(REFRESH) is None

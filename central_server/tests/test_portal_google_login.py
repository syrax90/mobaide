"""Tests for ``POST /portal/auth/google`` — the portal Google sign-in route.

The route's contract is:

  * CSRF (double-submit) is required.
  * A short-TTL httponly cookie (``portal_google_nonce``) carries the
    server-issued nonce; the GIS callback POSTs the matching ID token in
    ``credential``.
  * The cookie nonce must equal the ID-token ``nonce`` claim; the token
    must be ≤5 minutes old (``iat`` freshness).
  * Success: ``303 See Other`` to ``/portal/account``, sets
    ``portal_token`` cookie, deletes the nonce cookie.
  * Missing credential OR missing cookie → 400 (re-renders login).
  * Invalid token / nonce mismatch / stale iat → 401 (re-renders login).

We don't hit real Google: ``verify_oauth2_token`` is monkey-patched per
test to return whatever ``idinfo`` shape the case requires. This mirrors
how ``test_account_deletion.py`` short-circuits the Google path without a
real ID-token round-trip.
"""
from __future__ import annotations

import time
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from central_server.agents.service import AgentService
from central_server.app import create_app
from central_server.auth.service import AuthService
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from central_server.manifest.store import ManifestStore
from central_server.pairing.service import PairingService
from central_server.relay.routing import RoutingTable
from central_server.templates.service import TemplateService
from tests.conftest import (
    TEST_ADMIN_EMAIL,
    TEST_ADMIN_PASSWORD,
    TEST_JWT_SECRET,
)


_FAKE_CLIENT_ID = "fake-client.apps.googleusercontent.com"


@pytest.fixture
def google_config(tmp_path) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        admin_seed_email=TEST_ADMIN_EMAIL,
        admin_seed_password=TEST_ADMIN_PASSWORD,
        db_path=str(tmp_path / "google.db"),
        log_level="DEBUG",
        log_format="console",
        email_verification_required=False,
        google_client_id=_FAKE_CLIENT_ID,
        manifest_store_path=str(tmp_path / "manifest.json"),
    )


@pytest.fixture
async def google_db(google_config) -> Database:
    db = Database(google_config.db_path)
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
async def google_app(google_config, google_db):
    """FastAPI app wired exactly like conftest's ``app`` fixture, but with
    ``google_client_id`` set so the portal Google route works."""
    application = create_app(config=google_config)
    application.state.db = google_db
    application.state.routing = RoutingTable()
    application.state.manifest_store = ManifestStore(google_config.manifest_store_path)
    application.state.auth_service = AuthService(
        google_config, google_db, routing=application.state.routing,
    )
    application.state.pairing_service = PairingService(google_db)
    application.state.agent_service = AgentService(
        google_db, application.state.routing,
    )
    application.state.template_service = TemplateService(
        google_db, application.state.routing,
    )
    await application.state.auth_service.seed_admin_user()
    yield application


@pytest.fixture
async def google_client(google_app):
    transport = ASGITransport(app=google_app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


async def _grab_csrf_and_nonce(client) -> tuple[str, str]:
    """Hit ``/portal/login`` to harvest both the CSRF token AND the
    server-issued ``portal_google_nonce`` cookie. Returns
    ``(csrf_token, nonce)``.
    """
    resp = await client.get("/portal/login")
    assert resp.status_code == 200
    cookie_header = resp.headers.get("set-cookie", "")
    csrf_token = ""
    nonce = ""
    for piece in cookie_header.split(","):
        piece = piece.strip()
        if piece.startswith("portal_csrf="):
            csrf_token = piece.split(";", 1)[0].removeprefix("portal_csrf=")
        elif piece.startswith("portal_google_nonce="):
            nonce = piece.split(";", 1)[0].removeprefix("portal_google_nonce=")
    assert csrf_token, f"portal_csrf missing in: {cookie_header!r}"
    assert nonce, f"portal_google_nonce missing in: {cookie_header!r}"
    return csrf_token, nonce


def _patch_verify(monkeypatch, idinfo: dict[str, Any]) -> None:
    """Replace ``google.oauth2.id_token.verify_oauth2_token`` with a stub
    that returns the supplied ``idinfo`` payload. ``google_authenticate``
    calls it via ``run_in_executor`` with positional args; the stub
    accepts ``*args, **kwargs`` so we don't pin the call shape."""
    from google.oauth2 import id_token as google_id_token

    def _fake_verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return idinfo

    monkeypatch.setattr(
        google_id_token, "verify_oauth2_token", _fake_verify,
    )


def _patch_verify_raises(monkeypatch, exc: Exception) -> None:
    from google.oauth2 import id_token as google_id_token

    def _fake_verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise exc

    monkeypatch.setattr(
        google_id_token, "verify_oauth2_token", _fake_verify,
    )


class TestPortalGoogleAuthSuccess:
    async def test_successful_signin_sets_portal_cookie_and_redirects(
        self, google_client, monkeypatch,
    ) -> None:
        """A first-time Google sign-in (new user) returns 303 to
        /portal/account, sets the portal session cookie, and clears the
        nonce cookie. ``google_authenticate`` creates the user on first
        login transparently — no separate signup step needed."""
        csrf_token, nonce = await _grab_csrf_and_nonce(google_client)
        _patch_verify(
            monkeypatch,
            {
                "sub": "google-sub-1",
                "email": "newuser@gmail.com",
                "email_verified": True,
                "nonce": nonce,
                "iat": int(time.time()),
            },
        )

        resp = await google_client.post(
            "/portal/auth/google",
            data={"credential": "fake-id-token", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert resp.headers["location"] == "/portal/account"

        set_cookie = resp.headers.get("set-cookie", "")
        assert "portal_token=" in set_cookie
        # Nonce cookie is single-use — server deletes it on success.
        assert "portal_google_nonce=" in set_cookie  # delete sets empty value

    async def test_successful_signin_creates_user_row_with_google_sub(
        self, google_client, google_app, monkeypatch,
    ) -> None:
        """First-login Google sign-in must persist a user row with the
        Google sub so subsequent sign-ins resolve to the same account
        (the create-or-link path)."""
        csrf_token, nonce = await _grab_csrf_and_nonce(google_client)
        _patch_verify(
            monkeypatch,
            {
                "sub": "google-sub-2",
                "email": "fresh@gmail.com",
                "email_verified": True,
                "nonce": nonce,
                "iat": int(time.time()),
            },
        )

        resp = await google_client.post(
            "/portal/auth/google",
            data={"credential": "tok", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        user = await google_app.state.db.get_user_by_google_sub("google-sub-2")
        assert user is not None
        assert user["email"] == "fresh@gmail.com"


class TestPortalGoogleAuthRejection:
    async def test_nonce_mismatch_returns_401_and_no_cookie(
        self, google_client, monkeypatch,
    ) -> None:
        """ID-token nonce ≠ cookie nonce → 401, no portal_token set, no
        user created."""
        csrf_token, _real_nonce = await _grab_csrf_and_nonce(google_client)
        _patch_verify(
            monkeypatch,
            {
                "sub": "g-evil",
                "email": "attacker@gmail.com",
                "email_verified": True,
                "nonce": "different-nonce-from-attacker",
                "iat": int(time.time()),
            },
        )

        resp = await google_client.post(
            "/portal/auth/google",
            data={"credential": "stolen-token", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 401
        set_cookie = resp.headers.get("set-cookie", "")
        assert "portal_token=" not in set_cookie

    async def test_stale_iat_returns_401(
        self, google_client, monkeypatch,
    ) -> None:
        """``iat`` older than ``_GOOGLE_REAUTH_FRESHNESS_SECONDS`` (5 min) →
        401. Defends against replay of a token a user obtained hours ago."""
        csrf_token, nonce = await _grab_csrf_and_nonce(google_client)
        _patch_verify(
            monkeypatch,
            {
                "sub": "g-stale",
                "email": "stale@gmail.com",
                "email_verified": True,
                "nonce": nonce,
                "iat": int(time.time()) - 10 * 60,  # 10 min ago
            },
        )

        resp = await google_client.post(
            "/portal/auth/google",
            data={"credential": "old-token", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 401

    async def test_invalid_id_token_returns_401(
        self, google_client, monkeypatch,
    ) -> None:
        """Whatever Google's verifier raises is mapped to 401 — never 500."""
        csrf_token, _ = await _grab_csrf_and_nonce(google_client)
        _patch_verify_raises(monkeypatch, ValueError("bad signature"))

        resp = await google_client.post(
            "/portal/auth/google",
            data={"credential": "junk", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 401


class TestPortalGoogleAuthInputValidation:
    async def test_missing_credential_returns_400(
        self, google_client,
    ) -> None:
        """No ``credential`` form field (e.g. GIS callback never fired) →
        400 with the login template re-rendered."""
        csrf_token, _ = await _grab_csrf_and_nonce(google_client)

        resp = await google_client.post(
            "/portal/auth/google",
            data={"csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 400

    async def test_missing_nonce_cookie_returns_400(
        self, google_client,
    ) -> None:
        """Without ever visiting ``/portal/login`` (or after the nonce
        cookie expired), the POST must fail closed at 400 — the route
        cannot validate freshness without the cookie."""
        # Hit a CSRF-issuing GET on a route that does NOT issue the Google
        # nonce cookie. Picking ``/portal/delete-account-info`` keeps the
        # CSRF cookie populated while leaving ``portal_google_nonce`` unset.
        resp_csrf = await google_client.get("/portal/delete-account-info")
        cookie_header = resp_csrf.headers.get("set-cookie", "")
        csrf_token = ""
        for piece in cookie_header.split(","):
            piece = piece.strip()
            if piece.startswith("portal_csrf="):
                csrf_token = piece.split(";", 1)[0].removeprefix("portal_csrf=")
                break
        assert csrf_token

        # Confirm the nonce cookie is absent from the jar — if a previous
        # request did set it, this test would silently pass on the happy
        # branch; assert before exercising the failure.
        assert "portal_google_nonce" not in {
            c.name for c in google_client.cookies.jar
        }

        resp = await google_client.post(
            "/portal/auth/google",
            data={"credential": "tok", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 400

    async def test_missing_csrf_returns_403(self, google_client) -> None:
        """CSRF is enforced before any Google verification. No double-submit
        pair → 403, regardless of the body shape."""
        # Seed the cookie jar via a GET so the nonce cookie exists; the POST
        # still misses the CSRF header / form field.
        await _grab_csrf_and_nonce(google_client)

        resp = await google_client.post(
            "/portal/auth/google",
            data={"credential": "x"},
            follow_redirects=False,
        )

        assert resp.status_code == 403


class TestPasswordFormErrorsWithGoogleConfigured:
    """The password form's own error re-renders, exercised with Google enabled.

    ``portal_login.html`` and ``portal_signup.html`` mount the GIS button behind
    ``{% if google_client_id %}``, and the partial does
    ``{{ google_nonce | tojson }}``. Any re-render that forgets
    ``issue_google_nonce=True`` therefore hands Jinja an ``Undefined`` and raises
    ``TypeError: Object of type Undefined is not JSON serializable`` — a 500 on
    the most common error path on the sign-in page.

    The default ``client`` fixture leaves ``google_client_id`` unset, so the
    partial renders nothing there and cannot catch this. These tests exist
    specifically to cover the configured case, which is production.
    """

    async def test_wrong_password_rerenders_the_login_page(
        self, google_client
    ) -> None:
        csrf_token, _nonce = await _grab_csrf_and_nonce(google_client)

        resp = await google_client.post(
            "/portal/login",
            data={
                "csrf_token": csrf_token,
                "email": "nobody@example.test",
                "password": "definitely-wrong",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 401
        assert "Invalid email or password." in resp.text
        # A fresh nonce must reach the re-rendered GIS button, otherwise the
        # Google path on the error page is dead even when the page renders.
        assert "portal_google_nonce" in resp.headers.get("set-cookie", "")

    async def test_mismatched_passwords_rerender_the_signup_page(
        self, google_client
    ) -> None:
        csrf_token, _nonce = await _grab_csrf_and_nonce(google_client)

        resp = await google_client.post(
            "/portal/signup",
            data={
                "csrf_token": csrf_token,
                "email": "new-user@example.test",
                "password": "hunter2hunter2",
                "confirm_password": "hunter2hunter3",
            },
            follow_redirects=False,
        )

        assert resp.status_code == 400
        assert "Passwords don&#39;t match." in resp.text

    async def test_missing_fields_rerender_the_signup_page(
        self, google_client
    ) -> None:
        csrf_token, _nonce = await _grab_csrf_and_nonce(google_client)

        resp = await google_client.post(
            "/portal/signup",
            data={"csrf_token": csrf_token, "email": "", "password": ""},
            follow_redirects=False,
        )

        assert resp.status_code == 400
        assert "Email and password are required." in resp.text

    async def test_duplicate_email_rerenders_the_signup_page(
        self, google_client
    ) -> None:
        csrf_token, _nonce = await _grab_csrf_and_nonce(google_client)
        dupe = {
            "csrf_token": csrf_token,
            "email": "dupe@example.test",
            "password": "hunter2hunter2",
            "confirm_password": "hunter2hunter2",
        }
        first = await google_client.post(
            "/portal/signup", data=dupe, follow_redirects=False
        )
        assert first.status_code == 303

        # Reuse the same token rather than re-harvesting: ``ensure_csrf_cookie``
        # reuses a valid cookie instead of re-issuing it, so a second
        # ``_grab_csrf_and_nonce`` would find no ``portal_csrf`` in Set-Cookie.
        resp = await google_client.post(
            "/portal/signup", data=dupe, follow_redirects=False
        )

        assert resp.status_code == 409
        assert "Email already registered" in resp.text

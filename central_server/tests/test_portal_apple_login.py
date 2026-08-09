"""Tests for ``POST /portal/auth/apple`` — the portal Sign in with Apple route.

Structurally the twin of ``test_portal_google_login.py``, because the route is
the twin of the Google one: same CSRF guard, same server-issued nonce cookie
(``portal_signin_nonce``, shared by both providers now), same 303 to
``/portal/account``.

Apple's own redirect mode would arrive here cross-site, where ``SameSite=Lax``
withholds the CSRF and nonce cookies both; ``portal.js`` therefore runs the SDK
in popup mode and drops the ``id_token`` into a same-origin form POST. That is
what these tests replay.

No network: ``AuthService._decode_apple_id_token`` is monkeypatched per test,
the same seam the rest of the Apple suite uses. The audience handling the
decoder skips has its own coverage in ``test_apple_auth.TestAppleAudiences``.
"""
from __future__ import annotations

import time
from html.parser import HTMLParser
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

# Only the Services ID is configured: the portal never sees a native token, and
# a web-only instance has to work.
_SERVICES_ID = "com.mobaide.app.web"
_NATIVE_ID = "com.mobaide.app"
_GOOGLE_CLIENT_ID = "1234567890-portal.apps.googleusercontent.com"
_APPLE_SUB = "001999.fedcba9876543210.4321"


@pytest.fixture
def apple_web_config(tmp_path) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        admin_seed_email=TEST_ADMIN_EMAIL,
        admin_seed_password=TEST_ADMIN_PASSWORD,
        db_path=str(tmp_path / "apple_portal.db"),
        log_level="DEBUG",
        log_format="console",
        email_verification_required=False,
        apple_web_client_id=_SERVICES_ID,
        manifest_store_path=str(tmp_path / "manifest.json"),
    )


@pytest.fixture
async def apple_web_db(apple_web_config) -> Database:
    db = Database(apple_web_config.db_path)
    await db.connect()
    yield db
    await db.close()


def _wire_app(config: CentralServerConfig, db: Database):
    """Build an app with the state the portal routes read.

    The ASGI lifespan does not run under ``ASGITransport``, so the same
    hand-wiring ``tests/conftest.py`` does is repeated here — this module
    needs several *different* configs, which the shared fixture cannot give.
    """
    application = create_app(config=config)
    application.state.db = db
    application.state.routing = RoutingTable()
    application.state.manifest_store = ManifestStore(config.manifest_store_path)
    application.state.auth_service = AuthService(
        config, db, routing=application.state.routing,
    )
    application.state.pairing_service = PairingService(db)
    application.state.agent_service = AgentService(db, application.state.routing)
    application.state.template_service = TemplateService(
        db, application.state.routing,
    )
    return application


@pytest.fixture
async def apple_web_app(apple_web_config, apple_web_db):
    application = _wire_app(apple_web_config, apple_web_db)
    await application.state.auth_service.seed_admin_user()
    yield application


@pytest.fixture
async def apple_web_client(apple_web_app):
    transport = ASGITransport(app=apple_web_app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


@pytest.fixture
def native_only_config(tmp_path) -> CentralServerConfig:
    """An instance that ships the iOS app but not the web Services ID."""
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        db_path=str(tmp_path / "apple_native_only.db"),
        log_level="DEBUG",
        log_format="console",
        email_verification_required=False,
        apple_client_id=_NATIVE_ID,
        apple_web_client_id=None,
        manifest_store_path=str(tmp_path / "manifest_native.json"),
    )


@pytest.fixture
async def native_only_db(native_only_config) -> Database:
    db = Database(native_only_config.db_path)
    await db.connect()
    yield db
    await db.close()


@pytest.fixture
async def native_only_client(native_only_config, native_only_db):
    application = _wire_app(native_only_config, native_only_db)
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


async def _grab_csrf_and_nonce(client) -> tuple[str, str]:
    """Harvest the CSRF token and the ``portal_signin_nonce`` a login render
    issues. One nonce per render serves both providers."""
    resp = await client.get("/portal/login")
    assert resp.status_code == 200
    cookie_header = resp.headers.get("set-cookie", "")
    csrf_token = ""
    nonce = ""
    for piece in cookie_header.split(","):
        piece = piece.strip()
        if piece.startswith("portal_csrf="):
            csrf_token = piece.split(";", 1)[0].removeprefix("portal_csrf=")
        elif piece.startswith("portal_signin_nonce="):
            nonce = piece.split(";", 1)[0].removeprefix("portal_signin_nonce=")
    assert csrf_token, f"portal_csrf missing in: {cookie_header!r}"
    assert nonce, f"portal_signin_nonce missing in: {cookie_header!r}"
    return csrf_token, nonce


def _idinfo(nonce: str, **overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": "https://appleid.apple.com",
        "aud": _SERVICES_ID,
        "sub": _APPLE_SUB,
        "email": "portal-apple@example.com",
        "email_verified": True,
        "nonce": nonce,
        "iat": int(time.time()),
    }
    claims.update(overrides)
    return claims


def _patch_decode(monkeypatch, idinfo: dict[str, Any]) -> None:
    monkeypatch.setattr(
        AuthService, "_decode_apple_id_token", lambda self, tok: dict(idinfo)
    )


def _patch_decode_raises(monkeypatch, exc: Exception) -> None:
    def _raise(self, tok):
        raise exc

    monkeypatch.setattr(AuthService, "_decode_apple_id_token", _raise)


class TestPortalAppleAuthSuccess:
    async def test_successful_signin_sets_portal_cookie_and_redirects(
        self, apple_web_client, monkeypatch,
    ) -> None:
        csrf_token, nonce = await _grab_csrf_and_nonce(apple_web_client)
        _patch_decode(monkeypatch, _idinfo(nonce))

        resp = await apple_web_client.post(
            "/portal/auth/apple",
            data={"credential": "apple-id-token", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert resp.headers["location"] == "/portal/account"
        set_cookie = resp.headers.get("set-cookie", "")
        assert "portal_token=" in set_cookie
        # Single-use: the delete emits the cookie with an empty value and
        # Max-Age=0. Leaving it in place would let the credential be replayed
        # against the next render.
        assert 'portal_signin_nonce=""' in set_cookie
        assert "Max-Age=0" in set_cookie

    async def test_successful_signin_creates_a_user_row_with_the_apple_sub(
        self, apple_web_client, apple_web_db, monkeypatch,
    ) -> None:
        """The sub is what a later sign-in — on either surface — resolves
        against, so a session minted without persisting it would hand the next
        visit a brand-new account."""
        csrf_token, nonce = await _grab_csrf_and_nonce(apple_web_client)
        _patch_decode(monkeypatch, _idinfo(nonce))

        resp = await apple_web_client.post(
            "/portal/auth/apple",
            data={"credential": "tok", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        user = await apple_web_db.get_user_by_apple_sub(_APPLE_SUB)
        assert user is not None
        assert user["email"] == "portal-apple@example.com"


class TestPortalAppleAuthRejection:
    async def test_nonce_mismatch_returns_401_and_no_session(
        self, apple_web_client, apple_web_db, monkeypatch,
    ) -> None:
        """A token minted against somebody else's page render is a replay: the
        cookie nonce is the only thing tying this credential to this browser."""
        csrf_token, _real_nonce = await _grab_csrf_and_nonce(apple_web_client)
        _patch_decode(monkeypatch, _idinfo("nonce-from-another-render"))

        resp = await apple_web_client.post(
            "/portal/auth/apple",
            data={"credential": "stolen-token", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 401
        assert "portal_token=" not in resp.headers.get("set-cookie", "")
        assert await apple_web_db.get_user_by_apple_sub(_APPLE_SUB) is None

    async def test_invalid_id_token_returns_401(
        self, apple_web_client, monkeypatch,
    ) -> None:
        """Whatever PyJWT raises is mapped to 401 — never a 500."""
        csrf_token, _nonce = await _grab_csrf_and_nonce(apple_web_client)
        _patch_decode_raises(monkeypatch, ValueError("bad signature"))

        resp = await apple_web_client.post(
            "/portal/auth/apple",
            data={"credential": "junk", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 401
        assert "Apple sign-in failed" in resp.text

    async def test_the_rejection_rerender_arms_the_button_again(
        self, apple_web_client, monkeypatch,
    ) -> None:
        """The error page mounts the same Apple button, and that button's nonce
        comes from the render. A re-render that skipped ``issue_signin_nonce``
        would leave a button that posts a credential no cookie can match — a
        dead sign-in path, with a clean 200 on the page itself."""
        csrf_token, _nonce = await _grab_csrf_and_nonce(apple_web_client)
        _patch_decode_raises(monkeypatch, ValueError("bad signature"))

        resp = await apple_web_client.post(
            "/portal/auth/apple",
            data={"credential": "junk", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert "portal_signin_nonce=" in resp.headers.get("set-cookie", "")
        assert 'data-apple-nonce=""' not in resp.text


class TestPortalAppleAuthInputValidation:
    async def test_missing_credential_returns_400(self, apple_web_client) -> None:
        csrf_token, _nonce = await _grab_csrf_and_nonce(apple_web_client)

        resp = await apple_web_client.post(
            "/portal/auth/apple",
            data={"csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 400

    async def test_missing_nonce_cookie_returns_400(self, apple_web_client) -> None:
        """Without the cookie there is nothing to compare the token's claim
        against, so the route has to fail closed rather than skip the check."""
        # A CSRF-issuing GET that does not issue the sign-in nonce.
        resp_csrf = await apple_web_client.get("/portal/delete-account-info")
        cookie_header = resp_csrf.headers.get("set-cookie", "")
        csrf_token = ""
        for piece in cookie_header.split(","):
            piece = piece.strip()
            if piece.startswith("portal_csrf="):
                csrf_token = piece.split(";", 1)[0].removeprefix("portal_csrf=")
                break
        assert csrf_token
        assert "portal_signin_nonce" not in {
            c.name for c in apple_web_client.cookies.jar
        }

        resp = await apple_web_client.post(
            "/portal/auth/apple",
            data={"credential": "tok", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 400

    async def test_missing_csrf_returns_403(self, apple_web_client) -> None:
        """CSRF is enforced ahead of any Apple verification."""
        await _grab_csrf_and_nonce(apple_web_client)

        resp = await apple_web_client.post(
            "/portal/auth/apple",
            data={"credential": "x"},
            follow_redirects=False,
        )

        assert resp.status_code == 403


class TestPortalAppleRouteRequiresTheWebClientId:
    async def test_a_native_only_instance_refuses_the_route(
        self, native_only_client, native_only_db, monkeypatch,
    ) -> None:
        """An instance carrying ``APPLE_CLIENT_ID`` alone renders no Apple button
        here and allows no Apple origin in the portal CSP, yet without this
        gate the route still mints a portal session from a bundle-id-audience
        token.
        """
        csrf_token, nonce = await _grab_csrf_and_nonce(native_only_client)
        _patch_decode(monkeypatch, _idinfo(nonce, aud=_NATIVE_ID))

        resp = await native_only_client.post(
            "/portal/auth/apple",
            data={"credential": "native-token", "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
            follow_redirects=False,
        )

        assert resp.status_code == 400
        assert "portal_token=" not in resp.headers.get("set-cookie", "")
        # And no account behind it either: the gate has to precede the
        # create-or-link, not merely withhold the cookie.
        assert await native_only_db.get_user_by_apple_sub(_APPLE_SUB) is None


class TestPortalAppleButtonMounting:
    @pytest.mark.parametrize("path", ("/portal/login", "/portal/signup"))
    async def test_a_configured_services_id_mounts_the_button_and_the_sdk(
        self, apple_web_client, path,
    ) -> None:
        """All configuration travels as ``data-`` attributes: ``/portal/*`` is
        served with a ``script-src`` carrying no ``'unsafe-inline'``, so an
        inline block would never run and the values would never arrive."""
        resp = await apple_web_client.get(path)

        assert resp.status_code == 200
        assert "appleid.cdn-apple.com" in resp.text
        assert 'action="/portal/auth/apple"' in resp.text
        assert f'data-apple-client-id="{_SERVICES_ID}"' in resp.text
        # The divider is Google's today and Apple's here — either provider
        # earns it.
        assert "auth-divider" in resp.text

    @pytest.mark.parametrize("path", ("/portal/login", "/portal/signup"))
    async def test_an_unconfigured_instance_mounts_nothing_apple(
        self, client, path,
    ) -> None:
        """The conftest config configures neither provider. Emitting Apple's
        SDK there would be a third-party script no button uses, and the CSP
        that instance earns does not allow it."""
        resp = await client.get(path)

        assert resp.status_code == 200
        assert "appleid" not in resp.text
        assert "/portal/auth/apple" not in resp.text


class _ModalInputs(HTMLParser):
    """Collects the delete modal's ``<input>`` tags and every rendered id.

    Substring assertions cannot say *which* radio carries ``checked`` or that
    exactly one hidden ``method`` exists, and those are the two things the
    generalised chooser gets wrong quietly.
    """

    def __init__(self) -> None:
        super().__init__()
        self.inputs: list[dict[str, str | None]] = []
        self.ids: set[str] = set()

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        element_id = attributes.get("id")
        if element_id:
            self.ids.add(element_id)
        if tag == "input":
            self.inputs.append(attributes)


def _hidden_method(modal: _ModalInputs) -> str | None:
    """The single-method form's hidden ``method``, or None when absent."""
    values = [
        attrs.get("value")
        for attrs in modal.inputs
        if attrs.get("type") == "hidden" and attrs.get("name") == "method"
    ]
    assert len(values) <= 1, f"more than one hidden method: {values}"
    return values[0] if values else None


def _method_radios(modal: _ModalInputs) -> dict[str | None, bool]:
    """Radio ``method`` values mapped to whether each carries ``checked``."""
    return {
        attrs.get("value"): "checked" in attrs
        for attrs in modal.inputs
        if attrs.get("type") == "radio" and attrs.get("name") == "method"
    }


@pytest.fixture
async def render_delete_modal(tmp_path):
    """Render ``GET /portal/account/delete-modal`` for one account shape.

    Each call gets its own app because the offerable-method flags read the
    *config*, not just the user row.
    """
    resources: list[tuple[Database, AsyncClient]] = []

    async def _render(
        *,
        password: bool = False,
        google_sub: str | None = None,
        apple_sub: str | None = None,
        google_client_id: str | None = _GOOGLE_CLIENT_ID,
        apple_web_client_id: str | None = _SERVICES_ID,
    ) -> _ModalInputs:
        index = len(resources)
        config = CentralServerConfig(
            jwt_secret_key=TEST_JWT_SECRET,
            db_path=str(tmp_path / f"modal_{index}.db"),
            log_level="DEBUG",
            log_format="console",
            email_verification_required=False,
            google_client_id=google_client_id,
            apple_web_client_id=apple_web_client_id,
            manifest_store_path=str(tmp_path / f"modal_manifest_{index}.json"),
        )
        db = Database(config.db_path)
        await db.connect()
        application = _wire_app(config, db)
        client = AsyncClient(
            transport=ASGITransport(app=application), base_url="https://test"
        )
        resources.append((db, client))

        email = f"modal-{index}@example.com"
        user_id = await db.create_user(
            email=email,
            password_hash="argon2-placeholder" if password else None,
            google_sub=google_sub,
            apple_sub=apple_sub,
            email_verified=True,
        )
        # The same call every social sign-in makes before dropping the result
        # into `portal_token`; a password-less account has no login_user path.
        token = application.state.auth_service._create_user_token(
            user_id, email, "viewer"
        )
        resp = await client.get(
            "/portal/account/delete-modal",
            headers={"Cookie": f"portal_token={token}"},
        )
        assert resp.status_code == 200, resp.text

        modal = _ModalInputs()
        modal.feed(resp.text)
        return modal

    yield _render

    for db, client in resources:
        await client.aclose()
        await db.close()


class TestDeleteModalMethods:
    """The modal's re-auth chooser is now computed, not hard-coded.

    ``methods`` is built from ``has_password`` / ``has_google`` /
    ``has_apple``, and the two social flags mean *linked **and** that
    provider's client id configured* — an unconfigured provider cannot mount a
    button, so offering it would be a re-auth panel with nothing in it.
    """

    async def test_password_only_account_gets_a_hidden_method(
        self, render_delete_modal,
    ) -> None:
        modal = await render_delete_modal(password=True)

        assert _hidden_method(modal) == "password"
        assert _method_radios(modal) == {}
        assert "reauth-password-panel" in modal.ids
        assert "reauth-google-panel" not in modal.ids
        assert "reauth-apple-panel" not in modal.ids
        assert "confirm-delete-btn" in modal.ids

    async def test_google_only_account_gets_a_hidden_google_method(
        self, render_delete_modal,
    ) -> None:
        """Before this change the hidden field was a literal ``"google"``
        fallback; now it has to be *chosen*, and the Google panel is what
        submits the form — the typed-email submit button belongs to the
        password path only."""
        modal = await render_delete_modal(google_sub="google-sub-only")

        assert _hidden_method(modal) == "google"
        assert _method_radios(modal) == {}
        assert "reauth-google-panel" in modal.ids
        assert "reauth-password-panel" not in modal.ids
        assert "reauth-apple-panel" not in modal.ids
        assert "confirm-delete-btn" not in modal.ids

    async def test_a_linked_provider_without_a_client_id_is_not_offered(
        self, render_delete_modal,
    ) -> None:
        """``google_sub`` alone is not enough: with no client id the GIS
        button cannot mount, so naming the method would leave the user
        pressing an empty panel."""
        modal = await render_delete_modal(
            google_sub="google-sub-unconfigured", google_client_id=None,
        )

        assert _hidden_method(modal) is None
        assert _method_radios(modal) == {}
        assert "reauth-google-panel" not in modal.ids
        assert "confirm-delete-btn" not in modal.ids

    async def test_apple_only_account_gets_a_hidden_apple_method(
        self, render_delete_modal,
    ) -> None:
        """The shape that had *no* submit control at all before: the hidden
        method fell back to ``"google"`` for an account with neither a
        password nor a Google link."""
        modal = await render_delete_modal(apple_sub=_APPLE_SUB)

        assert _hidden_method(modal) == "apple"
        assert _method_radios(modal) == {}
        assert "reauth-apple-panel" in modal.ids
        assert "confirm-delete-apple-btn" in modal.ids
        assert "reauth-password-panel" not in modal.ids
        assert "reauth-google-panel" not in modal.ids

    async def test_password_and_apple_offer_a_chooser_led_by_password(
        self, render_delete_modal,
    ) -> None:
        modal = await render_delete_modal(password=True, apple_sub=_APPLE_SUB)

        # A hidden field alongside the radios would post two values.
        assert _hidden_method(modal) is None
        assert _method_radios(modal) == {"password": True, "apple": False}
        assert "reauth-password-panel" in modal.ids
        assert "reauth-apple-panel" in modal.ids
        assert "confirm-delete-btn" in modal.ids

    async def test_google_and_apple_offer_a_chooser_led_by_google(
        self, render_delete_modal,
    ) -> None:
        """Two social methods, no password: both panels mount, the first
        listed method is the pre-selected one, and the submit button stays
        away — each provider's own control submits."""
        modal = await render_delete_modal(
            google_sub="google-sub-both", apple_sub=_APPLE_SUB,
        )

        assert _hidden_method(modal) is None
        assert _method_radios(modal) == {"google": True, "apple": False}
        assert "reauth-google-panel" in modal.ids
        assert "reauth-apple-panel" in modal.ids
        assert "reauth-password-panel" not in modal.ids
        assert "confirm-delete-btn" not in modal.ids

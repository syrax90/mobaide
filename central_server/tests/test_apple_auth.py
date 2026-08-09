"""Tests for POST /api/auth/apple and the method="apple" delete re-auth.

The JWKS fetch + signature verification is stubbed by monkeypatching
``AuthService._decode_apple_id_token`` (the Apple analogue of patching
``google_id_token.verify_oauth2_token``), so no network is ever touched.
"""
from __future__ import annotations

import sqlite3
import time
from dataclasses import replace

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from central_server.agents.service import AgentService
from central_server.app import create_app
from central_server.auth.models import DeleteAccountRequest, UserTokenPayload
from central_server.auth.service import AccountDeletionAuthError, AuthService
from central_server.config import CentralServerConfig
from central_server.db.database import Database
from central_server.manifest.store import ManifestStore
from central_server.pairing.service import PairingService
from central_server.relay.routing import RoutingTable
from central_server.templates.service import TemplateService

from .conftest import TEST_JWT_SECRET

APPLE_CLIENT_ID = "com.mobaide.app"
# The Apple *Services ID*: what a browser identity token carries as `aud`.
APPLE_WEB_CLIENT_ID = "com.mobaide.app.web"
NONCE = "a" * 64
APPLE_SUB = "001234.abcdef1234567890.5678"


def _idinfo(**overrides) -> dict:
    claims = {
        "iss": "https://appleid.apple.com",
        "aud": APPLE_CLIENT_ID,
        "sub": APPLE_SUB,
        "email": "apple-user@example.com",
        "email_verified": True,
        "nonce": NONCE,
        "iat": int(time.time()),
    }
    claims.update(overrides)
    return claims


def _patch_decode(monkeypatch, idinfo: dict) -> None:
    monkeypatch.setattr(
        AuthService, "_decode_apple_id_token", lambda self, tok: dict(idinfo)
    )


def _patch_decode_raises(monkeypatch, exc: Exception) -> None:
    def _raise(self, tok):
        raise exc

    monkeypatch.setattr(AuthService, "_decode_apple_id_token", _raise)


def _build_payload(user: dict) -> UserTokenPayload:
    return UserTokenPayload(
        sub=user["id"],
        email=user["email"],
        role=user["role"],
        token_type="dashboard",
        exp=None,
        iat=0,
    )


@pytest.fixture
def apple_config(tmp_path) -> CentralServerConfig:
    return CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        db_path=str(tmp_path / "apple.db"),
        log_level="DEBUG",
        log_format="console",
        email_verification_required=False,
        apple_client_id=APPLE_CLIENT_ID,
        manifest_store_path=str(tmp_path / "manifest.json"),
    )


@pytest.fixture
async def apple_db(apple_config) -> Database:
    database = Database(apple_config.db_path)
    await database.connect()
    yield database
    await database.close()


@pytest.fixture
async def apple_app(apple_config, apple_db):
    application = create_app(config=apple_config)
    application.state.db = apple_db
    application.state.routing = RoutingTable()
    application.state.manifest_store = ManifestStore(
        apple_config.manifest_store_path
    )
    application.state.auth_service = AuthService(
        apple_config, apple_db, routing=application.state.routing
    )
    application.state.pairing_service = PairingService(apple_db)
    application.state.agent_service = AgentService(
        apple_db, application.state.routing
    )
    application.state.template_service = TemplateService(
        apple_db, application.state.routing
    )
    yield application


@pytest.fixture
async def apple_client(apple_app):
    transport = ASGITransport(app=apple_app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


class TestAppleEndpoint:
    async def test_new_user_created(self, apple_client, apple_db, monkeypatch):
        _patch_decode(monkeypatch, _idinfo())
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["email"] == "apple-user@example.com"
        assert body["role"] == "viewer"
        assert body["status"] == "ok"

        user = await apple_db.get_user_by_apple_sub(APPLE_SUB)
        assert user is not None
        assert user["email"] == "apple-user@example.com"
        assert user["email_verified"] == 1

    async def test_returning_user_by_sub(self, apple_client, apple_db, monkeypatch):
        user_id = await apple_db.create_user(
            email="returning@example.com", apple_sub=APPLE_SUB, email_verified=True
        )
        # Repeat sign-ins may omit the email claim entirely.
        _patch_decode(monkeypatch, _idinfo(email=None, email_verified=None))
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == user_id
        assert resp.json()["email"] == "returning@example.com"

    async def test_links_existing_email_account_and_marks_verified(
        self, apple_client, apple_db, monkeypatch
    ):
        user_id = await apple_db.create_user(
            email="linkme@example.com", password_hash="x", email_verified=False
        )
        _patch_decode(monkeypatch, _idinfo(email="linkme@example.com"))
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == user_id
        user = await apple_db.get_user_by_id(user_id)
        assert user["apple_sub"] == APPLE_SUB
        assert user["email_verified"] == 1

    async def test_email_verified_string_coercion(
        self, apple_client, apple_db, monkeypatch
    ):
        _patch_decode(monkeypatch, _idinfo(email_verified="true"))
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 200
        user = await apple_db.get_user_by_apple_sub(APPLE_SUB)
        assert user["email_verified"] == 1

    async def test_returning_user_by_sub_backfills_email_verified(
        self, apple_client, apple_db, monkeypatch
    ):
        user_id = await apple_db.create_user(
            email="backfill@example.com", apple_sub=APPLE_SUB, email_verified=False
        )
        _patch_decode(monkeypatch, _idinfo(email="backfill@example.com"))
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == user_id
        user = await apple_db.get_user_by_id(user_id)
        assert user["email_verified"] == 1

    async def test_unverified_email_not_taken_creates_unverified_user(
        self, apple_client, apple_db, monkeypatch
    ):
        # email_verified=False skips the link-by-email pass; with the address
        # free, the fall-through must create the account *unverified*.
        _patch_decode(monkeypatch, _idinfo(email_verified=False))
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["status"] == "ok"
        user = await apple_db.get_user_by_apple_sub(APPLE_SUB)
        assert user is not None
        assert user["email"] == "apple-user@example.com"
        assert user["email_verified"] == 0

    async def test_nonce_mismatch_returns_401(self, apple_client, monkeypatch):
        _patch_decode(monkeypatch, _idinfo(nonce="b" * 64))
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 401
        assert "nonce mismatch" in resp.json()["detail"].lower()

    async def test_missing_nonce_claim_returns_401(self, apple_client, monkeypatch):
        _patch_decode(monkeypatch, _idinfo(nonce=None))
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 401
        assert "nonce mismatch" in resp.json()["detail"].lower()

    async def test_non_ascii_nonce_returns_401_not_500(
        self, apple_client, monkeypatch
    ):
        """``nonce`` is an unauthenticated request-body field with no charset
        constraint, and ``compare_digest`` *raises* ``TypeError`` on a ``str``
        holding a non-ASCII character rather than returning False.
        """
        _patch_decode(monkeypatch, _idinfo())

        resp = await apple_client.post(
            "/api/auth/apple",
            json={"id_token": "tok", "nonce": "nónce-Ω-" + "a" * 56},
        )

        assert resp.status_code == 401
        assert "nonce mismatch" in resp.json()["detail"].lower()

    async def test_non_string_nonce_claim_returns_401(
        self, apple_client, monkeypatch
    ):
        """The claim side is untyped too: ``_decode_apple_id_token`` returns
        whatever JSON the token carried, and a non-empty non-``str`` value
        clears the truthiness guard and reaches the comparison, where
        ``.encode`` does not exist. A malformed token is a 401, not a crash.
        """
        _patch_decode(monkeypatch, _idinfo(nonce=12345))

        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )

        assert resp.status_code == 401
        assert "nonce mismatch" in resp.json()["detail"].lower()

    async def test_stale_iat_returns_401(self, apple_client, monkeypatch):
        _patch_decode(monkeypatch, _idinfo(iat=int(time.time()) - 600))
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 401
        assert "too old" in resp.json()["detail"].lower()

    async def test_bad_audience_returns_401(self, apple_client, monkeypatch):
        _patch_decode_raises(
            monkeypatch, jwt.InvalidAudienceError("Audience doesn't match")
        )
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 401
        assert "invalid apple id token" in resp.json()["detail"].lower()

    async def test_not_configured_returns_401(self, client):
        # conftest `client` has apple_client_id=None; the guard fires before
        # any decode attempt, so no stub is needed.
        resp = await client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 401
        assert "not configured" in resp.json()["detail"].lower()

    async def test_no_email_for_unknown_sub_returns_401(
        self, apple_client, monkeypatch
    ):
        _patch_decode(monkeypatch, _idinfo(email=None))
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 401
        assert "did not provide an email" in resp.json()["detail"].lower()

    async def test_unverified_email_owned_by_other_account_returns_401(
        self, apple_client, apple_db, monkeypatch
    ):
        await apple_db.create_user(
            email="taken@example.com", password_hash="x", email_verified=True
        )
        _patch_decode(
            monkeypatch, _idinfo(email="taken@example.com", email_verified=False)
        )
        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )
        assert resp.status_code == 401
        assert "different account" in resp.json()["detail"].lower()

    async def test_email_already_bound_to_another_apple_identity_is_refused(
        self, apple_client, apple_db, monkeypatch
    ):
        """``link_apple_to_user`` is an unconditional UPDATE, so the
        link-by-verified-email branch would silently rebind an account that
        already carries a *different* ``apple_sub``. Fail loudly, change
        nothing.
        """
        user_id = await apple_db.create_user(
            email="two-identities@example.com",
            apple_sub="001111.firstidentity0000.1111",
            email_verified=True,
        )
        _patch_decode(monkeypatch, _idinfo(email="two-identities@example.com"))

        resp = await apple_client.post(
            "/api/auth/apple", json={"id_token": "tok", "nonce": NONCE}
        )

        assert resp.status_code == 401
        assert "different apple identity" in resp.json()["detail"].lower()
        user = await apple_db.get_user_by_id(user_id)
        assert user["apple_sub"] == "001111.firstidentity0000.1111"
        # Nor may the refusal leak into a second account for the same address.
        assert await apple_db.get_user_by_apple_sub(APPLE_SUB) is None

    async def test_missing_nonce_field_returns_422(self, apple_client):
        resp = await apple_client.post("/api/auth/apple", json={"id_token": "tok"})
        assert resp.status_code == 422


class TestAppleDeleteReauth:
    async def test_apple_reauth_success_schedules_deletion(
        self, apple_config, apple_db, monkeypatch
    ):
        svc = AuthService(apple_config, apple_db)
        user_id = await apple_db.create_user(
            email="apple-del@example.com", apple_sub=APPLE_SUB, email_verified=True
        )
        user = await apple_db.get_user_by_id(user_id)
        _patch_decode(monkeypatch, _idinfo(email="apple-del@example.com"))

        scheduled = await svc.request_account_deletion(
            _build_payload(user),
            source="android",
            proof=DeleteAccountRequest(method="apple", id_token="tok", nonce=NONCE),
        )
        assert scheduled is not None
        refreshed = await apple_db.get_user_by_id(user_id)
        assert refreshed["scheduled_deletion_at"] is not None

    async def test_apple_reauth_wrong_sub_rejected(
        self, apple_config, apple_db, monkeypatch
    ):
        svc = AuthService(apple_config, apple_db)
        user_id = await apple_db.create_user(
            email="apple-mismatch@example.com",
            apple_sub="different-sub",
            email_verified=True,
        )
        user = await apple_db.get_user_by_id(user_id)
        _patch_decode(monkeypatch, _idinfo())

        with pytest.raises(AccountDeletionAuthError) as excinfo:
            await svc.request_account_deletion(
                _build_payload(user),
                source="android",
                proof=DeleteAccountRequest(
                    method="apple", id_token="tok", nonce=NONCE
                ),
            )
        assert "does not match" in str(excinfo.value).lower()

    async def test_apple_reauth_rejects_user_without_apple_sub(
        self, apple_config, apple_db
    ):
        svc = AuthService(apple_config, apple_db)
        user_id = await apple_db.create_user(
            email="pw-only@example.com", password_hash="x", email_verified=True
        )
        user = await apple_db.get_user_by_id(user_id)

        with pytest.raises(AccountDeletionAuthError) as excinfo:
            await svc.request_account_deletion(
                _build_payload(user),
                source="android",
                proof=DeleteAccountRequest(
                    method="apple", id_token="tok", nonce=NONCE
                ),
            )
        assert "not linked" in str(excinfo.value).lower()

    async def test_apple_reauth_requires_apple_client_id_configured(
        self, config, apple_db
    ):
        # conftest `config` has apple_client_id=None; the user must carry an
        # apple_sub so the "not linked" guard passes first.
        svc = AuthService(config, apple_db)
        user_id = await apple_db.create_user(
            email="apple-unconfigured@example.com",
            apple_sub="sub-unconfigured",
            email_verified=True,
        )
        user = await apple_db.get_user_by_id(user_id)

        with pytest.raises(AccountDeletionAuthError) as excinfo:
            await svc.request_account_deletion(
                _build_payload(user),
                source="android",
                proof=DeleteAccountRequest(
                    method="apple", id_token="tok", nonce=NONCE
                ),
            )
        assert "not configured" in str(excinfo.value).lower()

    def test_delete_request_shape_requires_token_and_nonce(self):
        with pytest.raises(ValueError, match="id_token and nonce"):
            DeleteAccountRequest(method="apple")


class TestAppleSubStorage:
    async def test_get_and_link_round_trip(self, apple_db):
        user_id = await apple_db.create_user(
            email="roundtrip@example.com", password_hash="x"
        )
        assert await apple_db.get_user_by_apple_sub("sub-rt") is None

        await apple_db.link_apple_to_user(user_id, "sub-rt")
        user = await apple_db.get_user_by_apple_sub("sub-rt")
        assert user is not None
        assert user["id"] == user_id
        assert user["apple_sub"] == "sub-rt"

    async def test_migration_is_idempotent(self, tmp_path):
        db_path = str(tmp_path / "reopen.db")
        first = Database(db_path)
        await first.connect()
        user_id = await first.create_user(
            email="persist@example.com", apple_sub="sub-persist"
        )
        await first.close()

        second = Database(db_path)
        await second.connect()
        user = await second.get_user_by_apple_sub("sub-persist")
        assert user is not None
        assert user["id"] == user_id
        await second.close()

    async def test_apple_sub_unique_index_enforced(self, apple_db):
        # Uniqueness lives in the partial index, not the column (ALTER TABLE
        # cannot add UNIQUE), so assert the index actually enforces it.
        first = await apple_db.create_user(
            email="first-dup@example.com", password_hash="x"
        )
        second = await apple_db.create_user(
            email="second-dup@example.com", password_hash="x"
        )
        await apple_db.link_apple_to_user(first, "sub-dup")
        with pytest.raises(sqlite3.IntegrityError):
            await apple_db.link_apple_to_user(second, "sub-dup")

    async def test_upgrade_from_v13_runs_alter_table(self, tmp_path):
        """Genuine v13 -> v14 upgrade: drop the column so the ALTER TABLE
        branch actually executes.

        The reopen tests elsewhere seed the head schema and only roll the
        version pragma back, so their column-existence guard skips the ALTER
        — a broken statement would surface only on a real production upgrade.
        """
        db_path = str(tmp_path / "v13.db")
        db = Database(db_path)
        await db.connect()
        user_id = await db.create_user(
            email="pre-upgrade@example.com", password_hash="x"
        )
        await db.conn.execute("DROP INDEX idx_users_apple_sub")
        await db.conn.execute("ALTER TABLE users DROP COLUMN apple_sub")
        await db.conn.execute("PRAGMA user_version = 13")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == 18

        # Column restored by the ALTER, unique index restored by _SCHEMA.
        await db.link_apple_to_user(user_id, "sub-upgraded")
        user = await db.get_user_by_apple_sub("sub-upgraded")
        assert user is not None
        assert user["id"] == user_id

        index_cursor = await db.conn.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' AND name = 'idx_users_apple_sub'"
        )
        index_row = await index_cursor.fetchone()
        assert index_row is not None
        assert "UNIQUE" in index_row[0].upper()
        await db.close()

    async def test_upgrade_from_pre_v13_schema_without_apple_sub(self, tmp_path):
        """Pre-v13 DBs re-run executescript(_SCHEMA) inside EARLIER migration
        blocks (v<5, v<6, v<7, ...), each of which validates
        idx_users_apple_sub's column reference — so the apple_sub guard must
        run before the first block, not only inside v<14. Regression for the
        startup crash `no such column: apple_sub` on a genuine v7 -> head
        upgrade.
        """
        db_path = str(tmp_path / "pre_v13.db")
        db = Database(db_path)
        await db.connect()
        user_id = await db.create_user(
            email="v7-upgrade@example.com", password_hash="x"
        )
        await db.conn.execute("DROP INDEX idx_users_apple_sub")
        await db.conn.execute("ALTER TABLE users DROP COLUMN apple_sub")
        await db.conn.execute("PRAGMA user_version = 7")
        await db.conn.commit()
        await db.close()

        db = Database(db_path)
        await db.connect()
        cursor = await db.conn.execute("PRAGMA user_version")
        assert (await cursor.fetchone())[0] == 18

        col_cursor = await db.conn.execute("PRAGMA table_info(users)")
        cols = {r[1] for r in await col_cursor.fetchall()}
        assert "apple_sub" in cols

        user = await db.get_user_by_id(user_id)
        assert user is not None
        assert user["apple_sub"] is None
        await db.close()


class _FakeSigningKey:
    key = "fake-apple-public-key"


class _FakeJwkClient:
    """Stand-in for ``jwt.PyJWKClient`` — the JWKS fetch is blocking urllib."""

    def __init__(self, *args, **kwargs) -> None:
        pass

    def get_signing_key_from_jwt(self, token_str: str) -> _FakeSigningKey:
        return _FakeSigningKey()


class _AudienceEnforcingDecoder:
    """A ``jwt.decode`` that actually honours the ``audience=`` it is handed.

    Every other test in this module replaces ``_decode_apple_id_token``
    wholesale, which walks straight past the argument the native/web audience
    split lives in. Here the real method runs and only PyJWT's key handling is
    replaced, so the audience list has to reach an enforcer to matter.
    """

    def __init__(self, claims_by_token: dict[str, dict]) -> None:
        self._claims_by_token = claims_by_token
        self.audiences: list[list[str]] = []

    def __call__(self, token_str, key, *, algorithms, audience, issuer):
        # A bare string would make PyJWT match one id only, and would turn the
        # membership test below into a substring check.
        assert isinstance(audience, list), f"audience must be a list: {audience!r}"
        self.audiences.append(audience)
        claims = self._claims_by_token[token_str]
        if claims["aud"] not in audience:
            raise jwt.InvalidAudienceError("Audience doesn't match")
        if claims["iss"] != issuer:
            raise jwt.InvalidIssuerError("Invalid issuer")
        return dict(claims)


def _install_decoder(monkeypatch, claims_by_token) -> _AudienceEnforcingDecoder:
    decoder = _AudienceEnforcingDecoder(claims_by_token)
    monkeypatch.setattr(jwt, "PyJWKClient", _FakeJwkClient)
    monkeypatch.setattr(jwt, "decode", decoder)
    return decoder


class TestAppleAudiences:
    """One endpoint, two Apple identifiers.

    iOS tokens carry the bundle id and browser tokens the Services ID, and
    both must resolve to the same account — the whole point of grouping the
    Services ID under the app's primary App ID.
    """

    def _service(self, apple_config, apple_db, *, native, web) -> AuthService:
        return AuthService(
            replace(
                apple_config, apple_client_id=native, apple_web_client_id=web
            ),
            apple_db,
        )

    async def test_both_configured_ids_are_offered_to_pyjwt(
        self, apple_config, apple_db, monkeypatch
    ):
        decoder = _install_decoder(monkeypatch, {"native-tok": _idinfo()})
        svc = self._service(
            apple_config, apple_db, native=APPLE_CLIENT_ID, web=APPLE_WEB_CLIENT_ID
        )

        await svc.apple_authenticate("native-tok", nonce=NONCE)

        assert decoder.audiences == [[APPLE_CLIENT_ID, APPLE_WEB_CLIENT_ID]]

    async def test_a_browser_token_lands_on_the_account_ios_created(
        self, apple_config, apple_db, monkeypatch
    ):
        """Signing in on the web with the Apple ID already used in the app
        must reach the same user row, not a second account. Both tokens carry
        the same ``sub`` and differ only in ``aud``."""
        _install_decoder(
            monkeypatch,
            {
                "native-tok": _idinfo(),
                "web-tok": _idinfo(aud=APPLE_WEB_CLIENT_ID),
            },
        )
        svc = self._service(
            apple_config, apple_db, native=APPLE_CLIENT_ID, web=APPLE_WEB_CLIENT_ID
        )

        native_user, _role, _token, was_new = await svc.apple_authenticate(
            "native-tok", nonce=NONCE
        )
        web_user, _role, _token, was_new_again = await svc.apple_authenticate(
            "web-tok", nonce=NONCE
        )

        assert was_new is True
        assert was_new_again is False
        assert web_user == native_user

    async def test_a_browser_token_is_refused_without_the_services_id(
        self, apple_config, apple_db, monkeypatch
    ):
        """The state of the world before this change, and the reason it is
        needed: a deployment that configures only the bundle id rejects every
        web token."""
        decoder = _install_decoder(
            monkeypatch, {"web-tok": _idinfo(aud=APPLE_WEB_CLIENT_ID)}
        )
        svc = self._service(
            apple_config, apple_db, native=APPLE_CLIENT_ID, web=None
        )

        with pytest.raises(ValueError, match="Invalid Apple ID token"):
            await svc.apple_authenticate("web-tok", nonce=NONCE)

        assert decoder.audiences == [[APPLE_CLIENT_ID]]

    async def test_a_web_only_instance_authenticates(
        self, apple_config, apple_db, monkeypatch
    ):
        """The "is Apple configured" guard hangs off the audience list now, so
        a server that runs the web surface without shipping an iOS app is a
        configured server."""
        decoder = _install_decoder(
            monkeypatch, {"web-tok": _idinfo(aud=APPLE_WEB_CLIENT_ID)}
        )
        svc = self._service(
            apple_config, apple_db, native=None, web=APPLE_WEB_CLIENT_ID
        )

        user_id, _role, token, was_new = await svc.apple_authenticate(
            "web-tok", nonce=NONCE
        )

        assert was_new is True
        assert token
        assert (await apple_db.get_user_by_apple_sub(APPLE_SUB))["id"] == user_id
        assert decoder.audiences == [[APPLE_WEB_CLIENT_ID]]

    async def test_neither_configured_fails_before_any_decode(
        self, apple_config, apple_db, monkeypatch
    ):
        decoder = _install_decoder(
            monkeypatch, {"web-tok": _idinfo(aud=APPLE_WEB_CLIENT_ID)}
        )
        svc = self._service(apple_config, apple_db, native=None, web=None)

        with pytest.raises(ValueError, match="not configured"):
            await svc.apple_authenticate("web-tok", nonce=NONCE)

        assert decoder.audiences == []

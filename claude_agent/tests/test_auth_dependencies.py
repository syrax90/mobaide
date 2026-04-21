"""Unit tests for ``claude_mobile.agent.auth.dependencies``.

Covers ``get_current_user`` (FastAPI REST DI) and ``ws_authenticate``
(WebSocket handshake). HTTP route coverage for the auth endpoints themselves
lives in ``tests/test_auth.py`` — do NOT duplicate it here.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
import structlog
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from claude_mobile.agent.auth.dependencies import (
    WebSocketAuthError,
    get_current_user,
    ws_authenticate,
)
from claude_mobile.agent.auth.jwt_service import JwtService

SECRET = "unit-test-secret-do-not-use-in-prod"


# ---------------------------------------------------------------------------
# Structlog contextvars bleed between tests. Clear before and after every test
# so ``bind_request_context`` assertions are hermetic.
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_structlog_contextvars():
    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


_ = _clear_structlog_contextvars  # satisfy pyright "not accessed" hint


# ---------------------------------------------------------------------------
# Helpers — tiny FastAPI app for get_current_user, AsyncMock for WebSocket.
# ---------------------------------------------------------------------------


def _make_test_app(jwt_service: JwtService) -> FastAPI:
    """Build a minimal FastAPI app with a single protected endpoint."""
    app = FastAPI()
    app.state.jwt_service = jwt_service

    @app.get("/protected")
    async def protected(username: str = Depends(get_current_user)):
        # Echo the resolved username so tests can confirm the happy-path return.
        return {"username": username}

    # Route is registered via the decorator; local binding is unused at the
    # Python level. Dummy reference satisfies Pyright's "not accessed" hint.
    _ = protected
    return app


def _make_fake_ws(jwt_service: JwtService, token: str | None) -> MagicMock:
    """Build a fake WebSocket with just the surface ``ws_authenticate`` touches."""
    ws = MagicMock(name="WebSocket")
    ws.app = MagicMock()
    ws.app.state = MagicMock()
    ws.app.state.jwt_service = jwt_service
    ws.query_params = {"token": token} if token is not None else {}
    ws.close = AsyncMock()
    ws.accept = AsyncMock()
    return ws


# ===========================================================================
# get_current_user
# ===========================================================================


class TestGetCurrentUser:
    def test_missing_authorization_header_returns_401(self):
        svc = JwtService(SECRET)
        client = TestClient(_make_test_app(svc))

        resp = client.get("/protected")

        assert resp.status_code == 401
        # The WWW-Authenticate header is how the route signals Bearer auth.
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_non_bearer_scheme_returns_401(self):
        svc = JwtService(SECRET)
        client = TestClient(_make_test_app(svc))

        resp = client.get("/protected", headers={"Authorization": "Basic xyz"})

        assert resp.status_code == 401
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_bearer_with_garbage_token_returns_401(self):
        svc = JwtService(SECRET)
        client = TestClient(_make_test_app(svc))

        resp = client.get(
            "/protected", headers={"Authorization": "Bearer not.a.token"}
        )

        assert resp.status_code == 401
        detail = resp.json().get("detail", "")
        assert "Invalid token" in detail

    def test_valid_access_token_returns_username(self):
        svc = JwtService(SECRET)
        token = svc.create_access_token("alice")
        client = TestClient(_make_test_app(svc))

        resp = client.get(
            "/protected", headers={"Authorization": f"Bearer {token}"}
        )

        assert resp.status_code == 200
        assert resp.json() == {"username": "alice"}

    def test_refresh_token_rejected_with_invalid_token_detail(self):
        # Type-mismatch and expired tokens now share a single opaque
        # 401 body so the mobile client treats all auth failures
        # identically (the specific reason is logged server-side).
        svc = JwtService(SECRET)
        refresh = svc.create_refresh_token("alice")
        client = TestClient(_make_test_app(svc))

        resp = client.get(
            "/protected", headers={"Authorization": f"Bearer {refresh}"}
        )

        assert resp.status_code == 401
        assert resp.json().get("detail") == "Invalid token"
        assert resp.headers.get("www-authenticate") == "Bearer"

    def test_expired_access_token_returns_401(self):
        """Forge a past-dated token with a real jwt.encode."""
        svc = JwtService(SECRET)
        past = datetime.now(UTC) - timedelta(hours=1)
        payload = {
            "sub": "alice",
            "type": "access",
            "iat": past - timedelta(minutes=30),
            "exp": past,
        }
        expired = jwt.encode(payload, SECRET, algorithm="HS256")

        client = TestClient(_make_test_app(svc))
        resp = client.get(
            "/protected", headers={"Authorization": f"Bearer {expired}"}
        )

        assert resp.status_code == 401
        detail = resp.json().get("detail", "")
        assert "Invalid token" in detail

    def test_success_binds_username_into_structlog_contextvars(self):
        """Hit the protected route synchronously so contextvars stay bound."""
        svc = JwtService(SECRET)
        token = svc.create_access_token("alice")

        captured: dict[str, object] = {}

        app = FastAPI()
        app.state.jwt_service = svc

        @app.get("/capture")
        async def capture(username: str = Depends(get_current_user)):
            # Capture contextvars from INSIDE the request so the bind is still live.
            captured.update(structlog.contextvars.get_contextvars())
            return {"username": username}

        _ = capture  # silence Pyright false positive for decorator-only handler
        client = TestClient(app)
        resp = client.get(
            "/capture", headers={"Authorization": f"Bearer {token}"}
        )

        assert resp.status_code == 200
        assert captured.get("username") == "alice"


# ===========================================================================
# ws_authenticate
# ===========================================================================


class TestWsAuthenticate:
    async def test_missing_token_closes_4001_and_raises(self):
        svc = JwtService(SECRET)
        ws = _make_fake_ws(svc, token=None)

        with pytest.raises(WebSocketAuthError):
            await ws_authenticate(ws)

        ws.close.assert_awaited_once()
        kwargs = ws.close.await_args.kwargs
        assert kwargs.get("code") == 4001
        assert "Missing token" in kwargs.get("reason", "")
        ws.accept.assert_not_called()

    async def test_invalid_token_closes_4001_and_raises(self):
        svc = JwtService(SECRET)
        ws = _make_fake_ws(svc, token="not.a.token")

        with pytest.raises(WebSocketAuthError):
            await ws_authenticate(ws)

        ws.close.assert_awaited_once()
        assert ws.close.await_args.kwargs.get("code") == 4001
        ws.accept.assert_not_called()

    async def test_refresh_token_closes_4001_with_invalid_token_reason(self):
        # WS auth mirrors REST: any rejection (wrong type, expired,
        # forged signature) closes with a unified "Invalid token"
        # reason; the specific cause is logged server-side.
        svc = JwtService(SECRET)
        refresh = svc.create_refresh_token("alice")
        ws = _make_fake_ws(svc, token=refresh)

        with pytest.raises(WebSocketAuthError):
            await ws_authenticate(ws)

        ws.close.assert_awaited_once()
        kwargs = ws.close.await_args.kwargs
        assert kwargs.get("code") == 4001
        assert kwargs.get("reason") == "Invalid token"
        ws.accept.assert_not_called()

    async def test_valid_access_token_accepts_and_returns_username(self):
        svc = JwtService(SECRET)
        token = svc.create_access_token("alice")
        ws = _make_fake_ws(svc, token=token)

        username = await ws_authenticate(ws)

        assert username == "alice"
        ws.accept.assert_awaited_once()
        ws.close.assert_not_called()

    async def test_expired_access_token_closes_4001_and_raises(self):
        svc = JwtService(SECRET)
        past = datetime.now(UTC) - timedelta(hours=1)
        expired = jwt.encode(
            {
                "sub": "alice",
                "type": "access",
                "iat": past - timedelta(minutes=30),
                "exp": past,
            },
            SECRET,
            algorithm="HS256",
        )
        ws = _make_fake_ws(svc, token=expired)

        with pytest.raises(WebSocketAuthError):
            await ws_authenticate(ws)

        ws.close.assert_awaited_once()
        assert ws.close.await_args.kwargs.get("code") == 4001
        ws.accept.assert_not_called()

    async def test_valid_access_token_binds_structlog_contextvars(self):
        svc = JwtService(SECRET)
        token = svc.create_access_token("alice")
        ws = _make_fake_ws(svc, token=token)

        await ws_authenticate(ws)

        ctx = structlog.contextvars.get_contextvars()
        assert ctx.get("username") == "alice"

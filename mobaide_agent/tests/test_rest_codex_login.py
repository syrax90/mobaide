"""Tests for ``POST /api/codex/login`` — the in-app Codex API-key sign-in.

The ``client`` / ``auth_headers`` fixtures come from ``tests/conftest.py``; the
autouse ``_pin_codex_binary`` fixture there pins ``resolve_binary`` to the
literal ``"codex"`` so these tests never hit the real host resolver. The login
subprocess is never run — ``codex_login_with_api_key`` is monkeypatched at the
router's import site.
"""
from __future__ import annotations

from mobaide.agent.codex import router as codex_router
from mobaide.core import CodexBinaryNotFoundError


class TestCodexLoginSuccess:
    async def test_valid_key_returns_200_authenticated(
        self, app, client, auth_headers, monkeypatch,
    ):
        async def _ok(api_key, *, codex_bin, env, **_kw):
            return True, ""

        monkeypatch.setattr(codex_router, "codex_login_with_api_key", _ok)

        resp = await client.post(
            "/api/codex/login",
            headers=auth_headers,
            json={"api_key": "sk-valid-key"},
        )

        assert resp.status_code == 200
        assert resp.json() == {"authenticated": True}

    async def test_configured_binary_is_passed_to_login_helper(
        self, app, client, auth_headers, monkeypatch,
    ):
        """The endpoint must use the launcher's configured binary (not a fresh
        resolve) so the credential lands where the create-session guard reads."""
        captured: dict = {}

        async def _capture(api_key, *, codex_bin, env, **_kw):
            captured["codex_bin"] = codex_bin
            return True, ""

        monkeypatch.setattr(codex_router, "codex_login_with_api_key", _capture)

        resp = await client.post(
            "/api/codex/login",
            headers=auth_headers,
            json={"api_key": "sk-x"},
        )

        assert resp.status_code == 200
        # ``_pin_codex_binary`` (conftest) pins the resolver to "codex".
        assert captured["codex_bin"] == "codex"


class TestCodexLoginFailure:
    async def test_login_failure_returns_400_with_sanitized_detail(
        self, app, client, auth_headers, monkeypatch,
    ):
        # 400 (not 401): a rejected key must not trip the Android AuthInterceptor's
        # refresh-retry-on-401, which would re-run the login subprocess.
        async def _fail(api_key, *, codex_bin, env, **_kw):
            return False, "key rejected ***"

        monkeypatch.setattr(codex_router, "codex_login_with_api_key", _fail)

        resp = await client.post(
            "/api/codex/login",
            headers=auth_headers,
            json={"api_key": "sk-bad-key-1234567890"},
        )

        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert detail.startswith("Codex login failed:")
        # The submitted key must never echo back, even on the failure path.
        assert "sk-bad-key-1234567890" not in resp.text

    async def test_missing_codex_binary_returns_503(
        self, app, client, auth_headers, monkeypatch,
    ):
        """When the Codex binary cannot be located, the endpoint mirrors the
        sessions route: 503 (not 401) with a clean ``CLI not found`` detail."""
        def _raise(*_a, **_kw):
            raise CodexBinaryNotFoundError(searched_paths=["PATH:codex"])

        monkeypatch.setattr(app.state.codex_launcher, "resolve_binary", _raise)

        resp = await client.post(
            "/api/codex/login",
            headers=auth_headers,
            json={"api_key": "sk-x"},
        )

        assert resp.status_code == 503
        assert "not found" in resp.json()["detail"].lower()


class TestCodexLoginAuthAndValidation:
    async def test_no_jwt_returns_401_without_codex_marker(self, client):
        """An unauthenticated request is rejected by ``get_current_user`` — a
        plain 401 that must NOT carry the ``codex_auth_required`` marker (that
        marker is reserved for the *session-create* auth-required case)."""
        resp = await client.post(
            "/api/codex/login",
            json={"api_key": "sk-x"},
        )

        assert resp.status_code == 401
        assert "codex_auth_required" not in resp.text

    async def test_blank_api_key_returns_422_and_scrubs_input(
        self, client, auth_headers,
    ):
        """A blank key violates ``min_length=1``; the 422 body must not echo the
        raw ``api_key`` input (it is in the app's secret-scrub set)."""
        resp = await client.post(
            "/api/codex/login",
            headers=auth_headers,
            json={"api_key": ""},
        )

        assert resp.status_code == 422
        # ``api_key`` is scrubbed from validation errors -> no ``input`` echo.
        body = resp.text
        assert '"input":""' not in body
        assert '"input": ""' not in body

    async def test_malformed_api_key_value_is_scrubbed_from_422(
        self, client, auth_headers,
    ):
        """A wrong-typed ``api_key`` (list) trips validation; the offending raw
        value must not be reflected back to the caller."""
        resp = await client.post(
            "/api/codex/login",
            headers=auth_headers,
            json={"api_key": ["sk-leaked-secret-value"]},
        )

        assert resp.status_code == 422
        assert "sk-leaked-secret-value" not in resp.text

    async def test_body_level_validation_error_scrubs_nested_api_key(
        self, client, auth_headers,
    ):
        """A whole-body type error (a JSON array where an object is expected) is
        reported at ``loc=("body",)`` with the entire raw body as ``input`` — the
        field-name scrub alone can't see the nested ``api_key``. The handler must
        walk the payload and strip it anyway."""
        resp = await client.post(
            "/api/codex/login",
            headers=auth_headers,
            json=[{"api_key": "sk-body-level-leak-123456"}],
        )

        assert resp.status_code == 422
        assert "sk-body-level-leak-123456" not in resp.text

"""Tests for the pre-accept WS Origin gate (ws_origin + config.normalize_origin).

The rejection oracle throughout: a disallowed Origin must surface as
``WebSocketDisconnect(4403)`` *even when the request would also fail auth*
(no token, unknown pairing nonce). Seeing 4403 instead of 4001/4404 proves
the gate runs before authentication at every accept site.
"""
from __future__ import annotations

import dataclasses
import logging

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from central_server.config import normalize_origin
from central_server.ws_origin import WS_CLOSE_ORIGIN_FORBIDDEN

from tests.conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD

EVIL_ORIGIN = "https://evil.example"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("https://app.mobaide.com", "https://app.mobaide.com"),
        # Scheme and host case-fold per RFC 6454 §4.
        ("HTTPS://App.Mobaide.COM", "https://app.mobaide.com"),
        # Default ports are dropped so the spellings compare equal…
        ("https://app.mobaide.com:443", "https://app.mobaide.com"),
        ("http://example.com:80", "http://example.com"),
        # …but only for the scheme they are the default of.
        ("http://example.com:443", "http://example.com:443"),
        ("https://example.com:8443", "https://example.com:8443"),
        ("https://app.mobaide.com/", "https://app.mobaide.com"),
        # Bracketed IPv6 loopback must survive (the loopback allowance in
        # ws_origin matches on the normalized form).
        ("http://[::1]:5173", "http://[::1]:5173"),
        ("http://[::1]", "http://[::1]"),
        # The opaque origin browsers send from sandboxed contexts.
        ("null", None),
        ("", None),
        ("not-a-url", None),
        ("ftp://x.example", None),
        ("https://", None),
        ("https://x.example/path", None),
        ("https://x.example:99999", None),
        ("https://x.example:0", None),
        # "²".isdigit() is True but int("²") raises: the isascii() guard must
        # leave the pseudo-port un-stripped (no crash), so the value simply
        # never matches the allow-list.
        ("https://x:\u00b2", "https://x:\u00b2"),
    ],
)
def test_normalize_origin(raw, expected):
    assert normalize_origin(raw) == expected


def _reject_code(app, path: str, origin: str) -> int:
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(path, headers={"Origin": origin}):
            pass  # pragma: no cover — handshake must be denied
    return excinfo.value.code


@pytest.mark.parametrize(
    "path",
    [
        # Direct gate; would otherwise accept() before any auth frame.
        "/ws/agent-tunnel",
        # Via ws_authenticate_user; 4403 (not 4001) proves gate-before-auth.
        "/ws/user-events",
        # Via ws_authenticate_mobile; same ordering proof.
        "/relay/ws/session/agent-x/session-y",
        # Direct gate; 4403 (not 4404) proves gate-before-nonce-lookup.
        "/ws/pairing/unknown-nonce",
    ],
)
def test_disallowed_origin_rejected_pre_accept_at_every_ws_site(app, path):
    assert _reject_code(app, path, EVIL_ORIGIN) == WS_CLOSE_ORIGIN_FORBIDDEN


@pytest.mark.parametrize(
    "origin",
    [
        "https://app.mobaide.com",
        # Equivalent spelling of the default allow-list entry.
        "https://App.Mobaide.com:443",
        # Loopback is structurally allowed, any port, http or https.
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "https://127.0.0.1",
        "http://[::1]:5173",
    ],
)
def test_allowed_and_loopback_origins_fall_through_to_auth(app, origin):
    # Gate passed → the missing token is what closes the socket (4001).
    assert _reject_code(app, "/ws/user-events", origin) == 4001


@pytest.mark.parametrize(
    "origin",
    [
        # Loopback matching is exact-host, not prefix/suffix.
        "https://localhost.evil.example",
        "http://127.0.0.1.evil.example",
        # Allow-list matching too.
        "https://app.mobaide.com.evil.example",
        "null",
        "not a url",
    ],
)
def test_lookalike_and_malformed_origins_are_rejected(app, origin):
    code = _reject_code(app, "/ws/user-events", origin)
    assert code == WS_CLOSE_ORIGIN_FORBIDDEN


@pytest.mark.parametrize(
    "origin",
    [
        # Regression: before the isascii() guard, int("²") raised inside the
        # gate and the handshake died with a server exception, not 4403.
        "https://x:\u00b2",
        # The un-stripped pseudo-port keeps the host from collapsing to bare
        # "localhost", so it must not ride the loopback allowance either.
        "http://localhost:\u00b2",
    ],
)
def test_non_ascii_digit_port_closes_4403_instead_of_crashing(app, origin):
    # Sent as latin-1 bytes — 0xB2 is valid obs-text on the wire, which is
    # how an attacker-controlled Origin actually arrives; httpx refuses
    # non-ASCII str header values client-side.
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect(
            "/ws/user-events",
            headers={"origin": origin.encode("latin-1")},
        ):
            pass  # pragma: no cover — handshake must be denied
    assert excinfo.value.code == WS_CLOSE_ORIGIN_FORBIDDEN


def test_absent_origin_admits_native_clients(app):
    # Android/iOS/agent sockets send no Origin header; the agent tunnel is
    # the sensitive site because it accepts before its in-band auth frame.
    client = TestClient(app)
    with client.websocket_connect("/ws/agent-tunnel"):
        pass


async def test_allowed_origin_with_valid_token_connects(app):
    _uid, _role, token, _sched = await app.state.auth_service.login_user(
        TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
    )
    admin = await app.state.db.get_user_by_email(TEST_ADMIN_EMAIL)

    client = TestClient(app)
    with client.websocket_connect(
        f"/ws/user-events?token={token}",
        headers={"Origin": "https://app.mobaide.com"},
    ):
        assert len(app.state.routing.get_user_clients(admin["id"])) == 1


def test_custom_allow_list_replaces_the_default(app):
    app.state.config = dataclasses.replace(
        app.state.config, ws_allowed_origins=("https://partner.example",)
    )
    # Listed origin passes the gate (then fails auth on the missing token).
    assert _reject_code(app, "/ws/user-events", "https://partner.example") == 4001
    # The compiled-in default is not implicitly unioned in.
    assert (
        _reject_code(app, "/ws/user-events", "https://app.mobaide.com")
        == WS_CLOSE_ORIGIN_FORBIDDEN
    )


def test_rejected_pairing_path_redacts_nonce_to_8_char_prefix(app, caplog):
    # The pairing nonce is a single-use bearer credential embedded in the
    # path; the rejection WARN must log only its 8-char prefix (the pairing
    # router's own nonce[:8] convention), never the claimable value.
    nonce = "a1b2c3d4" + "f" * 56
    with caplog.at_level(logging.WARNING, logger="central_server.ws_origin"):
        code = _reject_code(app, f"/ws/pairing/{nonce}", EVIL_ORIGIN)
    assert code == WS_CLOSE_ORIGIN_FORBIDDEN
    assert nonce not in caplog.text
    [record] = [r for r in caplog.records if r.name == "central_server.ws_origin"]
    assert "path=/ws/pairing/a1b2c3d4…" in record.getMessage()



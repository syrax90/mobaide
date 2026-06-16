"""The relay must verify ``wss://`` TLS against an explicit certifi-backed SSL
context rather than the host interpreter's default trust store.

Root cause this guards (see the relay SSL cert-verify report): a python.org
macOS framework Python whose bundled *Install Certificates.command* never ran
has an empty CA store, so a bare ``websockets.connect(wss://...)`` fails with
``CERTIFICATE_VERIFY_FAILED``. Pinning
``ssl=create_default_context(cafile=certifi.where())`` at the call site fixes it
on every platform. These tests pin that contract and assert ``ws://`` stays
plaintext (``ssl`` must be ``None`` — ``websockets`` raises if a context is
passed to a non-TLS URL, e.g. a local dev relay).
"""
from __future__ import annotations

import ssl
from unittest.mock import MagicMock

import pytest

from mobaide.relay import client as relay_client
from mobaide.relay.client import RelayClient
from mobaide.relay.config import RelayConfig


class _StopConnect(Exception):
    """Raised from the fake connect's ``__aenter__`` to abort
    ``_run_connection`` right after the ``websockets.connect`` call, so only the
    connect kwargs are exercised (no auth/tunnel handshake)."""


class _FakeConnectCM:
    """Async context manager returned by the fake ``websockets.connect``."""

    async def __aenter__(self):
        raise _StopConnect

    async def __aexit__(self, *_):
        return False


def _install_fake_websockets(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Replace ``mobaide.relay.client.websockets`` with a stub recording the
    kwargs of the most recent ``connect`` call."""
    captured: dict = {"connect_kwargs": None, "connect_calls": 0}

    def _fake_connect(url, **kwargs):
        captured["connect_calls"] += 1
        captured["connect_kwargs"] = {"url": url, **kwargs}
        return _FakeConnectCM()

    fake_module = MagicMock()
    fake_module.connect = _fake_connect
    monkeypatch.setattr(relay_client, "websockets", fake_module)
    return captured


def _make_client(server_url: str) -> RelayClient:
    config = RelayConfig(
        server_url=server_url,
        agent_id="agent-1",
        agent_token="token-1",
    )
    return RelayClient(
        config=config,
        session_manager=MagicMock(),
        event_bus=MagicMock(),
        broadcaster=MagicMock(),
        app_state=MagicMock(),
    )


class TestRelayConnectSslContext:
    async def test_wss_passes_verifying_ssl_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_fake_websockets(monkeypatch)
        client = _make_client("wss://relay.test")

        with pytest.raises(_StopConnect):
            await client._run_connection()

        assert captured["connect_calls"] == 1
        kwargs = captured["connect_kwargs"]
        assert kwargs["url"] == "wss://relay.test/ws/agent-tunnel"
        ctx = kwargs.get("ssl")
        assert isinstance(ctx, ssl.SSLContext), (
            "wss:// relay connection must pass an explicit ssl.SSLContext so TLS "
            "verifies against certifi instead of the host's default (possibly "
            f"empty) store; got ssl={ctx!r}"
        )
        # Guard against a regression that passes a non-verifying context.
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    async def test_ws_passes_no_ssl_context(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _install_fake_websockets(monkeypatch)
        client = _make_client("ws://localhost:8080")

        with pytest.raises(_StopConnect):
            await client._run_connection()

        assert captured["connect_calls"] == 1
        kwargs = captured["connect_kwargs"]
        assert kwargs["url"] == "ws://localhost:8080/ws/agent-tunnel"
        assert kwargs.get("ssl") is None, (
            "ws:// (plaintext, e.g. a local dev relay) must pass ssl=None — "
            "websockets raises if a context is given to a non-TLS URL; "
            f"got ssl={kwargs.get('ssl')!r}"
        )

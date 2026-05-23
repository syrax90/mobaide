"""Tests for the shared URL-derivation helpers in ``mobaide.agent.install_paths``.

``endpoint_url_from_relay_server_url`` is the security-critical core that
turns a ``RELAY_SERVER_URL`` (operator-supplied) into a co-located HTTPS
endpoint. The trust-key fetch and the HTTPS-bootstrap update path both
route through it, so the user-info rejection / cleartext refusal /
scheme-rebuild guards live here. ``trust_key_url_from_relay_server_url``
and ``manifest_url_from_relay_server_url`` are thin wrappers we cover
once each — exhaustive coverage of the core stays in ``TestEndpointUrl``.
"""
from __future__ import annotations

import pytest

from mobaide.agent.install_paths import (
    MANIFEST_ENDPOINT_PATH,
    TRUST_KEY_ENDPOINT_PATH,
    endpoint_url_from_relay_server_url,
    manifest_url_from_relay_server_url,
    trust_key_url_from_relay_server_url,
)


class TestEndpointUrl:
    """Direct coverage of the shared core helper."""

    def test_wss_becomes_https(self):
        assert (
            endpoint_url_from_relay_server_url("wss://example.com", "/foo")
            == "https://example.com/foo"
        )

    def test_https_passthrough(self):
        assert (
            endpoint_url_from_relay_server_url("https://example.com", "/foo")
            == "https://example.com/foo"
        )

    def test_path_component_is_dropped(self):
        # The relay tunnel and the REST API live at the same origin's root.
        # A relay-path like ``/ws/agent-tunnel`` must not survive into the
        # HTTPS-bootstrap fetch URL.
        assert (
            endpoint_url_from_relay_server_url(
                "wss://example.com/ws/agent-tunnel", "/api/v1/agent/manifest.json",
            )
            == "https://example.com/api/v1/agent/manifest.json"
        )

    def test_custom_port_preserved(self):
        assert (
            endpoint_url_from_relay_server_url("wss://example.com:9000", "/foo")
            == "https://example.com:9000/foo"
        )

    def test_ipv6_host_is_bracketed(self):
        # ``urlparse('wss://[::1]:8443').hostname`` returns ``::1`` (no
        # brackets); the rebuild must reintroduce them so the resulting URL
        # remains valid per RFC 3986.
        assert (
            endpoint_url_from_relay_server_url("wss://[::1]:8443", "/foo")
            == "https://[::1]:8443/foo"
        )

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="not a valid URL"):
            endpoint_url_from_relay_server_url("not-a-url", "/foo")

    def test_unsupported_scheme_raises(self):
        with pytest.raises(ValueError, match="unsupported scheme"):
            endpoint_url_from_relay_server_url("ftp://example.com", "/foo")

    def test_userinfo_rejected(self):
        # The classic redirect vector: ``urlparse('wss://api@evil.example')``
        # produces ``netloc='api@evil.example'`` which DNS-resolves to
        # ``evil.example``. Catching this guarantees a misbehaving
        # operator-provided URL can't redirect the trust-key / manifest
        # fetch off-host.
        with pytest.raises(ValueError, match="user-info"):
            endpoint_url_from_relay_server_url(
                "wss://api.mobaide.com@evil.example", "/foo",
            )

    def test_userinfo_with_password_rejected(self):
        with pytest.raises(ValueError, match="user-info"):
            endpoint_url_from_relay_server_url(
                "wss://user:pass@example.com", "/foo",
            )

    def test_cleartext_ws_refused_by_default(self, monkeypatch):
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raising=False,
        )
        with pytest.raises(ValueError, match="cleartext"):
            endpoint_url_from_relay_server_url("ws://example.com", "/foo")

    def test_cleartext_http_refused_by_default(self, monkeypatch):
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raising=False,
        )
        with pytest.raises(ValueError, match="cleartext"):
            endpoint_url_from_relay_server_url("http://example.com", "/foo")

    def test_cleartext_ws_allowed_when_flag_set(self, monkeypatch):
        monkeypatch.setenv("MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", "1")
        assert (
            endpoint_url_from_relay_server_url("ws://example.com", "/foo")
            == "http://example.com/foo"
        )

    def test_insecure_flag_only_accepts_literal_one(self, monkeypatch):
        # ``"true"``, ``"yes"``, ``"0"``, ``""`` are all unsafe values an
        # operator might pick by accident — only the literal ``"1"`` opts in.
        for raw in ("0", "true", "yes", ""):
            monkeypatch.setenv(
                "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raw,
            )
            with pytest.raises(ValueError, match="cleartext"):
                endpoint_url_from_relay_server_url("ws://example.com", "/foo")


class TestTrustKeyWrapper:
    """The trust-key wrapper just plugs the canonical endpoint path."""

    def test_uses_canonical_endpoint_path(self):
        assert (
            trust_key_url_from_relay_server_url("wss://example.com")
            == f"https://example.com{TRUST_KEY_ENDPOINT_PATH}"
        )
        assert TRUST_KEY_ENDPOINT_PATH == "/api/v1/agent/trust_key"

    def test_propagates_userinfo_rejection(self):
        # Smoke-test that the wrapper inherits the core's security guards
        # rather than bypassing them through some local path.
        with pytest.raises(ValueError, match="user-info"):
            trust_key_url_from_relay_server_url("wss://a@b.example")


class TestManifestUrlWrapper:
    """The manifest wrapper is the new piece — the rest of the test surface
    for the HTTPS-bootstrap path depends on its derivation matching the
    server's published manifest endpoint exactly."""

    def test_uses_canonical_endpoint_path(self):
        assert (
            manifest_url_from_relay_server_url("wss://example.com")
            == f"https://example.com{MANIFEST_ENDPOINT_PATH}"
        )
        assert MANIFEST_ENDPOINT_PATH == "/api/v1/agent/manifest.json"

    def test_strips_relay_path_keeps_origin(self):
        # Mirrors the trust-key behaviour: the relay tunnel and the
        # manifest endpoint share an origin; the relay path is dropped.
        assert (
            manifest_url_from_relay_server_url("wss://example.com/ws/agent-tunnel")
            == "https://example.com/api/v1/agent/manifest.json"
        )

    def test_preserves_port(self):
        assert (
            manifest_url_from_relay_server_url("wss://example.com:8443")
            == "https://example.com:8443/api/v1/agent/manifest.json"
        )

    def test_propagates_cleartext_refusal(self, monkeypatch):
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raising=False,
        )
        with pytest.raises(ValueError, match="cleartext"):
            manifest_url_from_relay_server_url("ws://example.com")

    def test_propagates_userinfo_rejection(self):
        with pytest.raises(ValueError, match="user-info"):
            manifest_url_from_relay_server_url("wss://user@evil.example")

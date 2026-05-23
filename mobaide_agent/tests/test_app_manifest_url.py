"""Tests for the agent-app HTTPS-bootstrap helpers in
``mobaide.agent.app``: ``_resolve_manifest_url`` and
``_resolve_manifest_url_with_hint``.

The hint resolver is security-sensitive — it accepts an *unauthenticated*
suggestion from the server's ``incompatible`` rejection payload and must
not let that suggestion redirect the manifest fetch to an arbitrary host.
The signature is the trust anchor regardless, but a tight URL guard
prevents the HTTPS fetch from even reaching an attacker-controlled origin,
which (a) preserves bandwidth, (b) keeps logs clean, and (c) avoids
leaking the agent's User-Agent / IP to a third party on every 4002.
"""
from __future__ import annotations

from mobaide.agent.app import (
    _resolve_manifest_url,
    _resolve_manifest_url_with_hint,
)
from mobaide.relay.config import RelayConfig


def _config(server_url: str) -> RelayConfig:
    return RelayConfig(
        server_url=server_url,
        agent_id="agent-x",
        agent_token="token-y",
    )


class TestResolveManifestUrl:
    """Thin wrapper around ``manifest_url_from_relay_server_url`` —
    happy path + the fail-soft contract (``None`` on derivation
    failure, no raise into the lifespan)."""

    def test_wss_derives_https(self):
        url = _resolve_manifest_url(_config("wss://example.com"))
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_cleartext_returns_none_not_raises(self, monkeypatch):
        # The agent runs the resolver eagerly in the lifespan; raising
        # would block startup. Returning ``None`` cleanly disables the
        # HTTPS bootstrap path and the in-band relay update path still
        # works (which is what we want for ``ws://localhost`` dev mode).
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raising=False,
        )
        url = _resolve_manifest_url(_config("ws://example.com"))
        assert url is None

    def test_userinfo_returns_none_not_raises(self):
        url = _resolve_manifest_url(_config("wss://user@evil.example"))
        assert url is None


class TestResolveManifestUrlWithHint:
    """The hint resolver merges an optional server-supplied path with the
    env-derived origin. The guards: hint must (a) be non-empty, (b) start
    with a single ``/``, (c) not start with ``//`` (protocol-relative)."""

    def test_no_hint_returns_derived(self):
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"), None,
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_empty_hint_returns_derived(self):
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"), "",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_valid_path_hint_resolves_against_origin(self):
        # The server moved the manifest to a new path; the agent picks
        # it up automatically. urljoin against the derived URL gives us
        # the new path on the SAME origin.
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "/api/v2/agent/manifest.json",
        )
        assert url == "https://example.com/api/v2/agent/manifest.json"

    def test_absolute_url_hint_falls_back_to_derived(self):
        # An absolute URL hint could redirect the fetch off-host. Reject
        # silently (log a warning, but never let the bad value reach the
        # outbound request) and fall back to the env-derived URL.
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "https://evil.example/api/v1/agent/manifest.json",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_protocol_relative_hint_falls_back_to_derived(self):
        # ``//evil.example/...`` is the classic protocol-relative URL
        # that ``urljoin`` happily resolves against ``https://...`` by
        # adopting the new host. Explicitly reject the ``//`` prefix.
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "//evil.example/api/v1/agent/manifest.json",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_hint_without_leading_slash_falls_back_to_derived(self):
        # urljoin would interpret a bare ``api/...`` against
        # ``https://example.com/api/v1/agent/manifest.json`` as a
        # relative path replacing the last segment — useful if it
        # happened on purpose, but the contract says path-only with a
        # leading slash, so reject everything else.
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "api/v1/agent/manifest.json",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_derived_none_with_no_hint_returns_none(self, monkeypatch):
        # Dev-mode ``ws://localhost`` config without the escape hatch:
        # both the derivation fails AND no hint is set. The result is
        # ``None`` so the callback's ``if url is None: return`` branch
        # correctly disables the fetch.
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raising=False,
        )
        url = _resolve_manifest_url_with_hint(
            _config("ws://localhost"), None,
        )
        assert url is None

    def test_derived_none_with_valid_hint_still_returns_none(
        self, monkeypatch,
    ):
        # A valid hint is useless if the agent's relay URL can't even
        # produce an origin. ``urljoin`` needs a base — without one we
        # have no host to attach the hint to. Return ``None`` so the
        # caller can skip the fetch cleanly.
        monkeypatch.delenv(
            "MOBAIDE_AGENT_ALLOW_INSECURE_TRUST_KEY_FETCH", raising=False,
        )
        url = _resolve_manifest_url_with_hint(
            _config("ws://localhost"),
            "/api/v1/agent/manifest.json",
        )
        assert url is None


class TestResolveManifestUrlWithHintWhitespaceBypass:
    """A whitespace-prefixed hint must not redirect the bootstrap fetch off-host.

    ``urljoin`` strips leading ASCII whitespace (``\\t``, ``\\r``, ``\\n``,
    ``\\v``, ``\\f``, space) from the reference URL before parsing per the
    WHATWG URL spec. Without the explicit whitespace guard, a hint like
    ``"/\\t/evil.example/path"`` would pass both ``startswith("/")`` (the
    first character IS ``/``) and ``not startswith("//")`` (the second
    character is ``\\t``, not ``/``), then resolve via ``urljoin`` to
    ``https://evil.example/path`` — silently redirecting the bootstrap
    fetch to an attacker-controlled host. The signature would still need
    to verify against the pinned trust key, but the agent has already
    leaked its User-Agent + IP + TLS client hello on every 4002 reject.
    """

    def test_tab_after_leading_slash_falls_back(self):
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "/\t/evil.example/api/v1/agent/manifest.json",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_cr_after_leading_slash_falls_back(self):
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "/\r/evil.example/api/v1/agent/manifest.json",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_lf_after_leading_slash_falls_back(self):
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "/\n/evil.example/api/v1/agent/manifest.json",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_space_after_leading_slash_falls_back(self):
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "/ /evil.example/api/v1/agent/manifest.json",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_nul_anywhere_falls_back(self):
        # Defence-in-depth: NUL in URLs is RFC-illegal but some libraries
        # tolerate it. Reject upfront.
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "/api\x00/v1/agent/manifest.json",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_leading_whitespace_then_double_slash_falls_back(self):
        # The protocol-relative variant — explicit double slash hidden
        # behind a tab. Both the whitespace guard AND the
        # ``startswith("//")`` guard would catch this; assert the
        # combined behaviour holds.
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "\t//evil.example/api/v1/agent/manifest.json",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

    def test_trailing_whitespace_falls_back(self):
        # The dangerous case is leading whitespace, but trailing
        # whitespace is equally illegitimate for a path and we reject
        # it for symmetry — keeps the guard simple ("no whitespace
        # anywhere") rather than position-dependent.
        url = _resolve_manifest_url_with_hint(
            _config("wss://example.com"),
            "/api/v1/agent/manifest.json\n",
        )
        assert url == "https://example.com/api/v1/agent/manifest.json"

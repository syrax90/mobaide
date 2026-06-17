"""Tests for ``mobaide._http`` — the User-Agent-injecting urllib wrapper.

The wrapper exists so every outbound HTTP call carries a descriptive
``User-Agent: mobaide-agent/<version>`` header, bypassing Cloudflare's
default Bot Fight Mode block of ``Python-urllib/X.Y`` (error 1010).
Tests verify both the injection and the non-override semantics callers
rely on.
"""
from __future__ import annotations

import urllib.request
from unittest.mock import patch

import pytest

import mobaide._http as _http


class _FakeResp:
    def __init__(self, body: bytes = b"{}"):
        self.status = 200
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


def _capture_request():
    captured: dict = {}

    def _fake(req, *, timeout, context=None):
        captured["request"] = req
        captured["timeout"] = timeout
        captured["context"] = context
        return _FakeResp()

    return captured, patch("urllib.request.urlopen", side_effect=_fake)


class TestUserAgentInjection:
    def test_injects_user_agent_for_url_string(self):
        captured, p = _capture_request()
        with p:
            with _http.urlopen("https://example.test/path", timeout=10) as _:
                pass

        req = captured["request"]
        assert isinstance(req, urllib.request.Request)
        assert req.get_header("User-agent") == _http.USER_AGENT

    def test_injects_user_agent_for_request_instance(self):
        captured, p = _capture_request()
        with p:
            req_in = urllib.request.Request(
                "https://example.test/path",
                headers={"Content-Type": "application/json"},
                method="POST",
                data=b"{}",
            )
            with _http.urlopen(req_in, timeout=10) as _:
                pass

        req = captured["request"]
        assert req is req_in
        assert req.get_header("User-agent") == _http.USER_AGENT

    def test_user_agent_starts_with_agent_prefix(self):
        # Sanity: prefix is well-known so operators can write WAF allow-rules
        # against it (e.g., ``User-Agent ~= ^mobaide-agent/``).
        assert _http.USER_AGENT.startswith("mobaide-agent/")


class TestUserAgentNotOverridden:
    def test_explicit_user_agent_in_headers_preserved(self):
        captured, p = _capture_request()
        with p:
            with _http.urlopen(
                "https://example.test/path",
                headers={"User-Agent": "custom/1.0"},
                timeout=10,
            ) as _:
                pass

        assert captured["request"].get_header("User-agent") == "custom/1.0"

    def test_lowercase_user_agent_in_headers_preserved(self):
        captured, p = _capture_request()
        with p:
            with _http.urlopen(
                "https://example.test/path",
                headers={"user-agent": "custom/1.0"},
                timeout=10,
            ) as _:
                pass

        assert captured["request"].get_header("User-agent") == "custom/1.0"

    def test_user_agent_already_on_request_preserved(self):
        captured, p = _capture_request()
        with p:
            req_in = urllib.request.Request("https://example.test/path")
            req_in.add_header("User-Agent", "preset/0.1")
            with _http.urlopen(req_in, timeout=10) as _:
                pass

        assert captured["request"].get_header("User-agent") == "preset/0.1"

    def test_user_agent_via_unredirected_header_preserved(self):
        captured, p = _capture_request()
        with p:
            req_in = urllib.request.Request("https://example.test/path")
            req_in.add_unredirected_header("User-Agent", "unredir/0.1")
            with _http.urlopen(req_in, timeout=10) as _:
                pass

        # add_unredirected_header writes to a separate dict; the wrapper
        # must consult both. ``get_header`` falls back through both.
        assert captured["request"].get_header("User-agent") == "unredir/0.1"


class TestForwarding:
    def test_data_method_headers_forwarded_to_request(self):
        captured, p = _capture_request()
        with p:
            with _http.urlopen(
                "https://example.test/api/x",
                data=b'{"k":"v"}',
                headers={"Content-Type": "application/json"},
                method="POST",
                timeout=42,
            ) as _:
                pass

        req = captured["request"]
        assert req.get_method() == "POST"
        assert req.data == b'{"k":"v"}'
        assert req.get_header("Content-type") == "application/json"
        assert captured["timeout"] == 42

    def test_request_path_disallows_extra_kwargs(self):
        # Mixing a pre-built Request with data/headers/method is ambiguous
        # (would they merge? overwrite?) — fail-fast at the wrapper boundary.
        req_in = urllib.request.Request("https://example.test/path")
        with pytest.raises(TypeError):
            _http.urlopen(req_in, data=b"x", timeout=10)
        with pytest.raises(TypeError):
            _http.urlopen(req_in, headers={"X": "y"}, timeout=10)
        with pytest.raises(TypeError):
            _http.urlopen(req_in, method="POST", timeout=10)

    def test_timeout_required(self):
        # Keyword-only and required: prevents accidentally blocking the
        # asyncio event loop with a default-blocking urlopen.
        with pytest.raises(TypeError):
            _http.urlopen("https://example.test/path")  # type: ignore[call-arg]


class TestTlsContext:
    def test_passes_certifi_pinned_context(self):
        # The updater's HTTPS downloads must verify against a certifi-pinned
        # context (mirroring relay/client.py) rather than the interpreter's
        # default CA store — empty on python.org framework builds (macOS), no
        # AIA chasing on Windows. Without this the updater depended on a
        # launcher-set SSL_CERT_FILE.
        import ssl

        captured, p = _capture_request()
        with p:
            with _http.urlopen("https://example.test/path", timeout=10):
                pass

        ctx = captured["context"]
        assert isinstance(ctx, ssl.SSLContext)
        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

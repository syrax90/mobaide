"""Tests for ``mobaide.agent.pairing_ws._parse_and_verify``.

We test the verification helper directly rather than the websocket
transport — the network-handling outer ``wait_for_claim`` is integration-
shaped (real WS server). The verification rules covered here are the
agent's defense against:

* tampered server pushes (signature must verify with pinned key),
* a server impersonating another relay (relay_url must match what the
  agent originally connected to),
* badly-clocked or clock-skewed pushes (issued_at within 5 min of now),
* malformed credentials (``_AGENT_CRED_RE`` rejects shell metas / NUL).
"""
from __future__ import annotations

import base64
import json
import logging
import types
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import mobaide.agent.pairing_ws as _pw
from mobaide.agent.pairing_ws import (
    PairingClaim,
    PairingWsClient,
    PairingWsError,
)


def _ed25519_keypair_b64() -> tuple[str, str]:
    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    return (
        base64.b64encode(priv.private_bytes_raw()).decode(),
        base64.b64encode(pub.public_bytes_raw()).decode(),
    )


def _canonical(payload: dict) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _signed_push(
    *,
    private_key_b64: str,
    issued_at: str | None = None,
    overrides: dict | None = None,
) -> dict:
    """Build a signed claim_completed push that ``_parse_and_verify`` accepts.

    Tests can partially override individual fields to exercise rejections.
    """
    priv = Ed25519PrivateKey.from_private_bytes(
        base64.b64decode(private_key_b64)
    )
    base = {
        "type": "claim_completed",
        "agent_id": "agent-12hex",
        "agent_token": "tok-urlsafe-32-bytes-1234567890",
        "relay_url": "https://relay.test",
        "agent_friendly_name": "lab-host",
        "issued_at": issued_at
        or datetime.now(timezone.utc).isoformat(),
    }
    if overrides:
        base.update(overrides)
    sig = priv.sign(_canonical(base))
    return {**base, "signature_b64": base64.b64encode(sig).decode()}


class TestParseAndVerifyHappyPath:
    def test_returns_pairing_claim(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(private_key_b64=priv_b64)

        claim = PairingWsClient._parse_and_verify(
            push,
            trust_pub_key_b64=pub_b64,
            expected_relay_url="https://relay.test",
        )

        assert isinstance(claim, PairingClaim)
        assert claim.agent_id == "agent-12hex"
        assert claim.agent_token == "tok-urlsafe-32-bytes-1234567890"
        assert claim.relay_url == "https://relay.test"
        assert claim.agent_friendly_name == "lab-host"

    def test_relay_url_trailing_slash_normalised(self):
        # Either side of the comparison may carry a trailing slash; the
        # check is rstrip("/")-normalised.
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"relay_url": "https://relay.test/"},
        )

        claim = PairingWsClient._parse_and_verify(
            push,
            trust_pub_key_b64=pub_b64,
            expected_relay_url="https://relay.test",
        )

        assert claim.relay_url == "https://relay.test/"


class TestParseAndVerifyRejections:
    def test_rejects_non_dict(self):
        with pytest.raises(PairingWsError, match="JSON object"):
            PairingWsClient._parse_and_verify(
                "not-a-dict",
                trust_pub_key_b64="aa",
                expected_relay_url="https://relay.test",
            )

    def test_rejects_unexpected_type(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"type": "definitely_not_claim_completed"},
        )

        with pytest.raises(PairingWsError, match="unexpected push type"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_missing_signature(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(private_key_b64=priv_b64)
        del push["signature_b64"]

        with pytest.raises(PairingWsError, match="missing signature_b64"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_tampered_payload(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(private_key_b64=priv_b64)
        # Tamper the agent_token AFTER signing.
        push["agent_token"] = "not-the-real-token"

        with pytest.raises(PairingWsError, match="signature verification"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_signature_with_wrong_key(self):
        # Push is signed with key A but verifier holds key B → reject.
        priv_a_b64, _ = _ed25519_keypair_b64()
        _, pub_b_b64 = _ed25519_keypair_b64()
        push = _signed_push(private_key_b64=priv_a_b64)

        with pytest.raises(PairingWsError, match="signature verification"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_relay_url_mismatch(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"relay_url": "https://relay.other"},
        )

        with pytest.raises(PairingWsError, match="relay_url mismatch"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_clock_skew_too_large(self):
        # ``issued_at`` more than 5 min off → reject.
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        skewed = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat()
        push = _signed_push(private_key_b64=priv_b64, issued_at=skewed)

        with pytest.raises(PairingWsError, match="clock skew"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_malformed_issued_at(self):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"issued_at": "not-an-iso-timestamp"},
        )

        with pytest.raises(PairingWsError, match="invalid issued_at"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    @pytest.mark.parametrize(
        "bad",
        [
            "tok with space",        # space rejected
            "tok\nwith-newline",     # newline rejected (smuggle .env line)
            "tok\rwith-cr",          # CR rejected
            "tok\x00with-nul",       # NUL rejected
            "tok!with-bang",         # ! not in regex
        ],
    )
    def test_rejects_credentials_failing_regex(self, bad):
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        push = _signed_push(
            private_key_b64=priv_b64,
            overrides={"agent_token": bad},
        )

        with pytest.raises(PairingWsError, match="agent_token failed"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

    def test_rejects_missing_required_field(self):
        # A push that is correctly signed but omits one required field
        # (server bug or unexpected schema drift). The signature must
        # cover the *exact* fields that the agent will read, so we sign
        # the truncated dict directly rather than mutating after sign.
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        priv = Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(priv_b64)
        )
        truncated = {
            "type": "claim_completed",
            # agent_id deliberately absent
            "agent_token": "tok-urlsafe-32-bytes-1234567890",
            "relay_url": "https://relay.test",
            "agent_friendly_name": "lab-host",
            "issued_at": datetime.now(timezone.utc).isoformat(),
        }
        sig = priv.sign(_canonical(truncated))
        push = {**truncated, "signature_b64": base64.b64encode(sig).decode()}

        with pytest.raises(PairingWsError, match="missing field"):
            PairingWsClient._parse_and_verify(
                push,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )


class _FakeWs:
    """Async-context-manager-yielded WebSocket stub with scripted ``recv``.

    ``recv_queue`` is consumed in order: each ``await ws.recv()`` returns
    the next entry. The queue carries ``str`` / ``bytes`` payloads (which
    ``wait_for_claim`` decodes itself) — the wire-shape contract the real
    ``websockets`` server emits.
    """

    def __init__(self, recv_queue: list):
        self._recv_queue = list(recv_queue)
        self.recv_calls = 0

    async def recv(self):
        self.recv_calls += 1
        if not self._recv_queue:
            raise AssertionError(
                "recv() called more times than the test scripted; "
                "extend ``recv_queue`` if a new wait_for_claim step "
                "legitimately needs another frame"
            )
        return self._recv_queue.pop(0)


class _FakeConnectCM:
    """Async context manager returned by the fake ``websockets.connect``.

    Holds onto the kwargs ``websockets.connect`` was called with so the
    test can assert on them after the function returns.
    """

    def __init__(self, ws: _FakeWs, captured: dict):
        self._ws = ws
        self._captured = captured

    async def __aenter__(self):
        return self._ws

    async def __aexit__(self, *_exc):
        return False


def _install_fake_websockets(
    monkeypatch: pytest.MonkeyPatch,
    *,
    ready_frame: str,
    claim_frame: str,
) -> dict:
    """Replace ``mobaide.agent.pairing_ws.websockets`` with a stub module.

    The stub exposes:

    * ``connect(url, **kwargs)`` — records the kwargs in the returned
      recorder dict and yields a ``_FakeWs`` with ``ready_frame`` then
      ``claim_frame`` in its recv queue.
    * ``WebSocketException`` — preserved from the real module so the
      production ``except (websockets.WebSocketException, ...)`` clause
      still type-checks. Without preserving it, the production
      ``except`` tuple would carry a non-Exception class and pytest
      would TypeError on collection.

    Returns the recorder dict carrying ``connect_kwargs`` (the kwargs of
    the most recent ``connect`` call) and ``connect_calls`` (call count).
    """
    captured: dict = {"connect_kwargs": None, "connect_calls": 0}

    def _fake_connect(url, **kwargs):
        captured["connect_calls"] += 1
        captured["connect_kwargs"] = {"url": url, **kwargs}
        ws = _FakeWs(recv_queue=[ready_frame, claim_frame])
        return _FakeConnectCM(ws, captured)

    fake_module = types.SimpleNamespace(
        connect=_fake_connect,
        # Preserve the real exception class so the production-side
        # ``except (websockets.WebSocketException, ...)`` clause keeps
        # type-checking. Reusing the real class is safer than minting a
        # local Exception subclass: the production code may catch any
        # ``websockets.exceptions.ConnectionClosed*`` variant, and those
        # all inherit from the real ``WebSocketException``.
        WebSocketException=_pw.websockets.WebSocketException,
    )
    monkeypatch.setattr(_pw, "websockets", fake_module)
    return captured


class TestWaitForClaimWebSocketsConnectKwargs:
    """Pin the explicit ``ping_interval`` / ``ping_timeout`` keepalive
    arguments threaded into ``websockets.connect``.

    The python-websockets default is also ~20 s, but the project's
    pyproject.toml only constrains ``websockets>=13.0`` — a later
    library version that changes the default would silently regress
    the WS-keepalive contract the agent relies on to surface
    Cloudflare-style idle drops as ``ConnectionClosedError`` within
    ~25 s. Pinning the kwargs at the call site makes the contract
    explicit and library-version-independent.
    """

    async def test_passes_ping_keepalive_to_websockets_connect(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Sign a valid claim_completed push so ``_parse_and_verify``
        # accepts it and ``wait_for_claim`` returns instead of raising
        # — proves the kwargs assertions run on a successful happy path,
        # not just because the WS error path bailed out before
        # ``connect`` was called.
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        signed = _signed_push(private_key_b64=priv_b64)

        captured = _install_fake_websockets(
            monkeypatch,
            ready_frame=json.dumps({"type": "ready"}),
            claim_frame=json.dumps(signed),
        )

        claim = await PairingWsClient().wait_for_claim(
            ws_url="wss://relay.test/ws/pairing/n1",
            timeout_s=30.0,
            trust_pub_key_b64=pub_b64,
            expected_relay_url="https://relay.test",
        )

        # Sanity: the function actually traversed both recv steps and
        # produced a valid claim. Without this the kwargs assertions
        # might pass on a no-op WS.
        assert isinstance(claim, PairingClaim)
        assert claim.agent_id == "agent-12hex"

        # The smoking-gun contract: ``websockets.connect`` was invoked
        # exactly once with both ping kwargs at the documented values.
        assert captured["connect_calls"] == 1, (
            f"expected exactly one websockets.connect call; got "
            f"{captured['connect_calls']}"
        )
        kwargs = captured["connect_kwargs"]
        assert kwargs is not None
        assert kwargs.get("ping_interval") == 20, (
            f"ping_interval must be pinned to 20s so a Cloudflare-style "
            f"idle drop surfaces as ConnectionClosedError within ~25s "
            f"instead of stalling until the nonce TTL (up to 120s); got "
            f"ping_interval={kwargs.get('ping_interval')!r}"
        )
        assert kwargs.get("ping_timeout") == 20, (
            f"ping_timeout must be pinned to 20s alongside ping_interval; "
            f"got ping_timeout={kwargs.get('ping_timeout')!r}"
        )
        # The pre-existing connect kwargs must still be threaded through
        # — pin them so a regression that replaces the call with a
        # truncated kwargs dict (and only kept ping_*) is caught.
        assert kwargs.get("open_timeout") == 15, (
            f"open_timeout=15 must remain on websockets.connect; "
            f"got {kwargs.get('open_timeout')!r}"
        )
        assert kwargs.get("close_timeout") == 5, (
            f"close_timeout=5 must remain on websockets.connect; "
            f"got {kwargs.get('close_timeout')!r}"
        )
        # The URL is passed positionally and routed verbatim — pin it so
        # a regression that mangles the URL on the way in (e.g. an
        # accidental f-string interpolation) is caught.
        assert kwargs["url"] == "wss://relay.test/ws/pairing/n1"


class TestWaitForClaimStructuredLogging:
    """Pin the new operator-visibility log lines added alongside the
    Windows event-loop fix.

    Operators previously had no way to tell — from ``agent.err.log``
    alone — which step of ``wait_for_claim`` had hung or failed
    (connect? ready? claim recv? signature verify?). The structured
    INFO log lines make every step grep-able. A single happy-path
    assertion is enough; the failure-branch log lines are covered
    incidentally via the existing
    ``test_pairing_monitor.TestRunPairingMonitorWsFailureHandling``
    tests and are not regression-critical in isolation.
    """

    async def test_happy_path_emits_structured_progress_logs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        priv_b64, pub_b64 = _ed25519_keypair_b64()
        signed = _signed_push(private_key_b64=priv_b64)
        _install_fake_websockets(
            monkeypatch,
            ready_frame=json.dumps({"type": "ready"}),
            claim_frame=json.dumps(signed),
        )

        with caplog.at_level(logging.INFO, logger=_pw.__name__):
            await PairingWsClient().wait_for_claim(
                ws_url="wss://relay.test/ws/pairing/n1",
                timeout_s=30.0,
                trust_pub_key_b64=pub_b64,
                expected_relay_url="https://relay.test",
            )

        info_messages = [
            r.getMessage()
            for r in caplog.records
            if r.levelno == logging.INFO
            and r.name == _pw.__name__
        ]
        # The four structured event names form the operator's
        # diagnostic chain. Pin all of them so a regression that
        # drops any single one (or renames it) is caught — a renamed
        # event silently breaks ops dashboards that grep for the
        # literal string.
        required_events = [
            "pairing_ws.connecting",
            "pairing_ws.ready_received",
            "pairing_ws.recv_claim_begin",
            "pairing_ws.claim_verified",
        ]
        for event in required_events:
            assert any(event in msg for msg in info_messages), (
                f"expected '{event}' in INFO logs on the happy path; "
                f"got: {info_messages!r}"
            )
        # The ``claim_verified`` line MUST carry the agent_id so the
        # operator can correlate the WS-side claim against the .env
        # value once the write step completes. Pin the agent_id from
        # the fixture's signed push.
        claim_verified_lines = [
            msg for msg in info_messages
            if "pairing_ws.claim_verified" in msg
        ]
        assert claim_verified_lines, (
            "claim_verified log line is the single most important "
            "operator-visibility signal — it proves the agent's WS "
            "actually received and validated a push. Missing it would "
            "have hidden the original Windows-Proactor recv hang"
        )
        assert any(
            "agent-12hex" in msg for msg in claim_verified_lines
        ), (
            f"expected claim agent_id in claim_verified log; got: "
            f"{claim_verified_lines!r}"
        )

"""Cover the protocol-version handshake on ``/ws/agent-tunnel``:

* Compatible ``protocol_version`` -> ``auth_ok`` carrying server version
  fields and (optional) manifest pointer.
* Unsupported ``protocol_version`` -> ``incompatible`` text frame followed
  by close code ``PROTOCOL_INCOMPATIBLE_CLOSE_CODE`` (4002), no agent
  registration.
* ``_coerce_int`` boundary cases for the field's permissive parser.
* The unknown-control-type ``else`` branch increments the module-level
  telemetry counter for forward-compat metrics.
"""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from central_server import __version__
from central_server.manifest.store import ManifestStore, canonical_json
from central_server.relay import agent_ws as agent_ws_module
from central_server.relay.agent_ws import _coerce_int
from central_server.relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
    PROTOCOL_INCOMPATIBLE_CLOSE_CODE,
    SUPPORTED_PROTOCOL_VERSIONS,
)


class TestCoerceInt:
    def test_int_passes_through(self):
        assert _coerce_int(7, default=1) == 7

    def test_negative_int_passes_through(self):
        # Range-checking is a separate concern; ``_coerce_int`` only normalizes
        # the type. The caller validates membership in SUPPORTED_PROTOCOL_VERSIONS.
        assert _coerce_int(-5, default=1) == -5

    def test_bool_falls_back_to_default(self):
        # ``bool`` subclasses ``int`` in Python; without the explicit guard,
        # ``True`` would be coerced to ``1`` and bypass the protocol check.
        assert _coerce_int(True, default=99) == 99
        assert _coerce_int(False, default=99) == 99

    def test_numeric_string_parses(self):
        assert _coerce_int("42", default=1) == 42

    def test_negative_numeric_string_parses(self):
        assert _coerce_int("-3", default=1) == -3

    def test_non_numeric_string_falls_back_to_default(self):
        assert _coerce_int("abc", default=88) == 88

    def test_none_falls_back_to_default(self):
        assert _coerce_int(None, default=5) == 5

    def test_float_falls_back_to_default(self):
        # Floats aren't part of the wire-protocol field spec; tolerate by
        # falling back rather than risk a silent rounding bug.
        assert _coerce_int(1.5, default=42) == 42


class TestAgentTunnelProtocolHandshake:
    def test_unsupported_protocol_version_closes_with_4002(
        self, app, registered_agent,
    ):
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 99,  # unsupported by this server
            }))
            # Server first sends the textual ``incompatible`` frame …
            incompat = json.loads(ws.receive_text())
            assert incompat["type"] == "incompatible"
            assert incompat["min_client_version"] == MIN_CLIENT_PROTOCOL_VERSION
            assert incompat["server_protocol_versions"] == list(
                SUPPORTED_PROTOCOL_VERSIONS,
            )
            # … then closes with the protocol-incompatible code.
            with pytest.raises(Exception):
                ws.receive_text()

        # The agent must NOT have been registered in routing — the 4002
        # close happens *before* register_agent in the production code.
        assert app.state.routing.is_agent_online(agent_id) is False

    def test_incompatible_payload_carries_manifest_url_hint(
        self, app, registered_agent,
    ):
        """The 4002 rejection JSON must include a relative ``manifest_url``
        pointing at the HTTPS-bootstrap endpoint so a wire-incompatible
        agent can self-heal without operator intervention.

        The path stays relative — the agent resolves it against the
        relay's own HTTPS origin (the trust anchor is the manifest
        signature). Future relocations of the endpoint only need a
        server-side change.
        """
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 99,
            }))
            incompat = json.loads(ws.receive_text())
            assert incompat["type"] == "incompatible"
            # Exact path — this is the contract the agent's
            # ``_resolve_manifest_url_with_hint`` validates against
            # ``hint.startswith("/")``.
            assert incompat["manifest_url"] == "/api/v1/agent/manifest.json"

    def test_compatible_protocol_version_emits_auth_ok_with_version_fields(
        self, app, registered_agent,
    ):
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 4,
                "agent_version": "0.1.0",
                "capabilities": {},
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "auth_ok"
            assert resp["server_version"] == __version__
            assert resp["server_protocol_versions"] == list(
                SUPPORTED_PROTOCOL_VERSIONS,
            )
            assert resp["min_client_protocol_version"] == MIN_CLIENT_PROTOCOL_VERSION
            assert resp["capabilities"] == {}
            # No manifest_store wired -> manifest fields stay null.
            assert resp["latest_agent_version"] is None
            assert resp["manifest_body"] is None
            assert resp["manifest_signature"] is None

    def test_missing_protocol_version_defaults_to_incompatible(
        self, app, registered_agent,
    ):
        # MIN_CLIENT_PROTOCOL_VERSION is 4 — the legacy default
        # (``protocol_version=1``) the parser falls back to is no longer in
        # ``SUPPORTED_PROTOCOL_VERSIONS``. A pre-v4 build with no
        # ``protocol_version`` field is correctly rejected here.
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
            }))
            incompat = json.loads(ws.receive_text())
            assert incompat["type"] == "incompatible"

    def test_auth_ok_carries_manifest_body_when_store_populated(
        self, app, registered_agent, tmp_path,
    ):
        agent_id, raw_token = registered_agent
        private = Ed25519PrivateKey.generate()
        body = {
            "version": "0.9.9",
            "tarball_url": "file:///tmp/agent.tar.gz",
            "tarball_sha256": "0" * 64,
            "wheel_url": "file:///tmp/agent.whl",
            "wheel_sha256": "1" * 64,
            "released_at": "2026-04-26T00:00:00+00:00",
        }
        sig_b64 = base64.b64encode(private.sign(canonical_json(body))).decode()
        store = ManifestStore(tmp_path / "manifest.json")
        store.write(body, sig_b64)
        app.state.manifest_store = store

        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 4,
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "auth_ok"
            assert resp["latest_agent_version"] == "0.9.9"
            assert resp["manifest_body"] == body
            assert resp["manifest_signature"] == sig_b64


class TestHandshakeVersionPersisted:
    """The auth handshake is the SoT for the agent's reported version.
    Cover the end-to-end DB write triggered by a successful auth, the
    COALESCE-preserves-existing semantic on a follow-up auth that omits
    the field, and the string-validation guard against non-string
    ``agent_version`` payloads.
    """

    async def test_successful_auth_persists_version_and_protocol(
        self, app, registered_agent, db,
    ):
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 4,
                "agent_version": "0.3.0",
            }))
            resp = json.loads(ws.receive_text())
            assert resp["type"] == "auth_ok"

        agent = await db.get_agent(agent_id)
        assert agent is not None
        assert agent["agent_version"] == "0.3.0"
        assert agent["agent_protocol_version"] == 4

    async def test_reauth_without_agent_version_preserves_stored_value(
        self, app, registered_agent, db,
    ):
        """Regression: a follow-up reconnect whose auth payload omits
        ``agent_version`` must not wipe the value persisted by an
        earlier handshake — the agent's relay reconnect logic relies
        on this COALESCE semantic so an in-progress upgrade where the
        binary briefly forgets to send the field doesn't erase the
        last good value the dashboard is showing.
        """
        agent_id, raw_token = registered_agent
        client = TestClient(app)

        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 4,
                "agent_version": "0.3.0",
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 4,
                # No agent_version field at all.
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        agent = await db.get_agent(agent_id)
        assert agent["agent_version"] == "0.3.0"
        assert agent["agent_protocol_version"] == 4

    async def test_non_string_agent_version_does_not_clobber(
        self, app, registered_agent, db,
    ):
        """The string-validation guard in agent_ws coerces a non-string
        (or empty-string) ``agent_version`` to None before calling
        ``update_agent_handshake_info``, so COALESCE preserves whatever
        was stored before. This test starts from a clean row (NULL) to
        prove the write itself skips the field — the value stays NULL
        rather than being persisted as ``"5"`` or ``""``.
        """
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 4,
                "agent_version": 5,  # int, not a string
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        agent = await db.get_agent(agent_id)
        assert agent["agent_version"] is None
        # Protocol version is still captured because it has its own
        # ``_coerce_int`` path, not the string guard.
        assert agent["agent_protocol_version"] == 4

    async def test_empty_string_agent_version_does_not_clobber(
        self, app, registered_agent, db,
    ):
        agent_id, raw_token = registered_agent
        client = TestClient(app)

        # First, prime the row with a real version.
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 4,
                "agent_version": "0.3.0",
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        # Then reconnect with an empty string — the guard should
        # treat this as "no value" (truthy check) and leave the prior
        # version untouched.
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 4,
                "agent_version": "",
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        agent = await db.get_agent(agent_id)
        assert agent["agent_version"] == "0.3.0"

    async def test_successful_auth_populates_last_seen_at(
        self, app, registered_agent, db,
    ):
        """Regression for the coalesced-write perf change: the connect
        path used to call ``touch_agent_last_seen`` separately before
        the version persistence; now ``update_agent_handshake_info``
        bumps ``last_seen_at`` itself in the same UPDATE+commit. Verify
        end-to-end that a fresh agent (``last_seen_at IS NULL`` after
        ``register_agent``) gets its ``last_seen_at`` populated by the
        auth handshake alone — no other write site on the connect path.
        """
        agent_id, raw_token = registered_agent

        before = await db.get_agent(agent_id)
        assert before is not None
        assert before["last_seen_at"] is None

        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": 4,
                "agent_version": "0.3.0",
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        after = await db.get_agent(agent_id)
        assert after["last_seen_at"] is not None
        assert after["agent_version"] == "0.3.0"
        assert after["agent_protocol_version"] == 4


class TestUnknownControlTypeTelemetry:
    async def test_unknown_type_increments_module_counter(self):
        # The handler's ``else`` branch is the only writer of
        # ``_unknown_control_type_total``; we exercise the JSON path
        # (``_handle_control_json``) directly rather than the binary path
        # because the production code routes them through the same handler.
        before = agent_ws_module._unknown_control_type_total
        await agent_ws_module._handle_control_json(
            websocket=None,
            agent_id="agent-test",
            ctrl={"type": "future.event"},
            routing=None,
        )
        await agent_ws_module._handle_control_json(
            websocket=None,
            agent_id="agent-test",
            ctrl={"type": "another_unknown"},
            routing=None,
        )
        after = agent_ws_module._unknown_control_type_total
        assert after - before == 2

    async def test_known_types_do_not_increment_counter(self, app, registered_agent):
        agent_id, _ = registered_agent
        before = agent_ws_module._unknown_control_type_total
        # ``heartbeat`` touches routing only (in-memory); provide a fake.

        class _FakeRouting:
            def agent_heartbeat(self, agent_id):
                pass

        await agent_ws_module._handle_control_json(
            websocket=None,
            agent_id=agent_id,
            ctrl={"type": "heartbeat"},
            routing=_FakeRouting(),
        )
        after = agent_ws_module._unknown_control_type_total
        assert after == before

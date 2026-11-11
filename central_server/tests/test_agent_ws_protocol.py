"""Cover the protocol-version handshake on ``/ws/agent-tunnel``:

* Compatible ``protocol_version`` -> ``auth_ok`` carrying server version
  fields and (optional) manifest pointer, and the negotiated version
  stamped on the live routing entry.
* Unsupported ``protocol_version`` -> ``incompatible`` text frame followed
  by close code ``PROTOCOL_INCOMPATIBLE_CLOSE_CODE`` (4002), no agent
  registration.
* The reported agent version and capabilities persisted on the agent row.
"""
from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from central_server import __version__
from central_server.manifest.store import ManifestStore, canonical_json
from central_server.relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
    PROTOCOL_INCOMPATIBLE_CLOSE_CODE,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)


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
                "protocol_version": PROTOCOL_VERSION,
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

    def test_handshake_stamps_negotiated_version_on_routing_entry(
        self, app, registered_agent,
    ):
        """The live ``AgentConnection`` carries the tunnel's own negotiated
        version — the source the relay's custom-kind 409 gate reads
        instead of the DB handshake row, which an overlapping tunnel can
        leave describing the other connection."""
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": PROTOCOL_VERSION,
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

            conn = app.state.routing.get_agent(agent_id)
            assert conn is not None
            assert conn.protocol_version == PROTOCOL_VERSION

    def test_missing_protocol_version_defaults_to_incompatible(
        self, app, registered_agent,
    ):
        # The legacy default (``protocol_version=1``) the parser falls back to
        # for a missing field is no longer in ``SUPPORTED_PROTOCOL_VERSIONS``, so
        # a pre-handshake build with no ``protocol_version`` is rejected here.
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
                "protocol_version": PROTOCOL_VERSION,
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
                "protocol_version": PROTOCOL_VERSION,
                "agent_version": "0.3.0",
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": PROTOCOL_VERSION,
                # No agent_version field at all.
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        agent = await db.get_agent(agent_id)
        assert agent["agent_version"] == "0.3.0"
        assert agent["agent_protocol_version"] == PROTOCOL_VERSION

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
                "protocol_version": PROTOCOL_VERSION,
                "agent_version": 5,  # int, not a string
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        agent = await db.get_agent(agent_id)
        assert agent["agent_version"] is None
        # Protocol version is still captured because it has its own
        # ``_coerce_int`` path, not the string guard.
        assert agent["agent_protocol_version"] == PROTOCOL_VERSION

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
                "protocol_version": PROTOCOL_VERSION,
                "agent_version": "0.3.0",
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        after = await db.get_agent(agent_id)
        assert after["last_seen_at"] is not None
        assert after["agent_version"] == "0.3.0"
        assert after["agent_protocol_version"] == PROTOCOL_VERSION


class TestAgentUninstallHandshake:
    """A removed agent asked to uninstall itself gets the command as its
    auth reply; the ``uninstall`` capability in the auth message is
    persisted for the clients' checkbox."""

    async def test_removed_agent_with_request_gets_agent_uninstall(
        self, app, registered_agent, db,
    ):
        agent_id, raw_token = registered_agent
        await db.record_uninstall_request(agent_id)
        await db.delete_agent(agent_id)

        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": PROTOCOL_VERSION,
            }))
            assert json.loads(ws.receive_text()) == {"type": "agent_uninstall"}
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_text()
            assert excinfo.value.code == 4001

        assert app.state.routing.is_agent_online(agent_id) is False

    async def test_wrong_token_for_removed_agent_is_a_plain_auth_failure(
        self, app, registered_agent, db,
    ):
        agent_id, _ = registered_agent
        await db.record_uninstall_request(agent_id)
        await db.delete_agent(agent_id)

        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": "guessed-token",
                "protocol_version": PROTOCOL_VERSION,
            }))
            with pytest.raises(WebSocketDisconnect) as excinfo:
                ws.receive_text()
            assert excinfo.value.code == 4001
            assert excinfo.value.reason == "Invalid agent credentials"

    @pytest.mark.parametrize(
        ("capabilities", "expected"),
        [
            ({"uninstall": True}, 1),
            ({"uninstall": "true"}, 0),
        ],
    )
    async def test_uninstall_capability_is_persisted_only_when_true(
        self, app, registered_agent, db, capabilities, expected,
    ):
        agent_id, raw_token = registered_agent
        client = TestClient(app)
        with client.websocket_connect("/ws/agent-tunnel") as ws:
            ws.send_text(json.dumps({
                "type": "auth",
                "agent_id": agent_id,
                "token": raw_token,
                "protocol_version": PROTOCOL_VERSION,
                "capabilities": capabilities,
            }))
            assert json.loads(ws.receive_text())["type"] == "auth_ok"

        assert (await db.get_agent(agent_id))["supports_uninstall"] == expected

"""Cover the protocol-versioning extensions to ``RelayClient``:

* ``_authenticate`` sends ``protocol_version`` / ``agent_version`` /
  ``capabilities`` and persists the manifest pointer from ``auth_ok``.
* ``_authenticate`` raises on an ``incompatible`` reply (so the connect loop
  can back off rather than silently fail).
* ``_handle_control`` dispatches ``agent_manifest_update`` to the registered
  callback and updates ``self._latest_manifest``.
* The ``else`` branch in ``_handle_control`` increments
  ``_unknown_control_type_total`` for forward-compat telemetry rather than
  silently dropping the frame.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from claude_mobile import __version__
from claude_mobile.relay.client import RelayClient
from claude_mobile.relay.envelope import PROTOCOL_VERSION


@pytest.fixture
def client(
    relay_config,
    mock_session_manager,
    mock_event_bus,
    mock_broadcaster,
    mock_app_state,
):
    return RelayClient(
        config=relay_config,
        session_manager=mock_session_manager,
        event_bus=mock_event_bus,
        broadcaster=mock_broadcaster,
        app_state=mock_app_state,
    )


class TestAuthenticateExtensions:
    async def test_auth_message_carries_version_fields(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({"type": "auth_ok"}))
        await client._authenticate(ws)

        ws.send.assert_called_once()
        sent = json.loads(ws.send.call_args[0][0])
        assert sent["type"] == "auth"
        assert sent["protocol_version"] == PROTOCOL_VERSION
        assert sent["agent_version"] == __version__
        assert sent["capabilities"] == {}

    async def test_auth_ok_persists_manifest_pointer(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({
            "type": "auth_ok",
            "latest_agent_version": "0.3.1",
            "manifest_url": "/api/v1/agent/manifest.json",
            "manifest_signature": "sig-b64-payload",
        }))
        await client._authenticate(ws)

        assert client.latest_manifest == {
            "version": "0.3.1",
            "manifest_url": "/api/v1/agent/manifest.json",
            "manifest_signature": "sig-b64-payload",
        }

    async def test_auth_ok_with_no_manifest_clears_pointer(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({"type": "auth_ok"}))
        await client._authenticate(ws)

        assert client.latest_manifest is not None
        assert client.latest_manifest["version"] is None
        assert client.latest_manifest["manifest_url"] is None
        assert client.latest_manifest["manifest_signature"] is None

    async def test_auth_incompatible_raises_connection_error(self, client):
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({
            "type": "incompatible",
            "reason": "Server speaks [2,3], agent sent 1",
            "min_client_version": 2,
            "server_protocol_versions": [2, 3],
        }))
        with pytest.raises(ConnectionError, match="protocol version"):
            await client._authenticate(ws)

    async def test_auth_ok_dispatches_manifest_callback(self, client):
        captured: list[tuple[object, object, object]] = []

        async def cb(url, signature, version):
            captured.append((url, signature, version))

        client.set_manifest_update_callback(cb)
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({
            "type": "auth_ok",
            "latest_agent_version": "0.4.0",
            "manifest_url": "https://central.example/manifest.json",
            "manifest_signature": "abc",
        }))
        await client._authenticate(ws)

        assert captured == [
            ("https://central.example/manifest.json", "abc", "0.4.0"),
        ]

    async def test_auth_ok_skips_callback_when_no_manifest(self, client):
        captured: list[object] = []

        async def cb(url, signature, version):
            captured.append((url, signature, version))

        client.set_manifest_update_callback(cb)
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({"type": "auth_ok"}))
        await client._authenticate(ws)

        assert captured == [], "callback fired with empty manifest pointer"

    async def test_auth_ok_swallows_callback_exception(self, client):
        async def cb(*_args):
            raise RuntimeError("downstream failure")

        client.set_manifest_update_callback(cb)
        ws = AsyncMock()
        ws.recv = AsyncMock(return_value=json.dumps({
            "type": "auth_ok",
            "latest_agent_version": "0.5.0",
            "manifest_url": "u",
            "manifest_signature": "s",
        }))
        await client._authenticate(ws)


class TestAgentManifestUpdateControl:
    async def test_agent_manifest_update_stores_pointer(self, client):
        ws = AsyncMock()
        ctrl = {
            "type": "agent_manifest_update",
            "version": "0.6.0",
            "manifest_url": "/api/v1/agent/manifest.json",
            "manifest_signature": "newsig",
        }
        await client._handle_control(json.dumps(ctrl).encode(), ws)

        assert client.latest_manifest == {
            "version": "0.6.0",
            "manifest_url": "/api/v1/agent/manifest.json",
            "manifest_signature": "newsig",
        }

    async def test_agent_manifest_update_fires_callback(self, client):
        captured: list[tuple[object, ...]] = []

        async def cb(url, signature, version):
            captured.append((url, signature, version))

        client.set_manifest_update_callback(cb)
        ws = AsyncMock()
        ctrl = {
            "type": "agent_manifest_update",
            "version": "0.7.0",
            "manifest_url": "/api/v1/agent/manifest.json",
            "manifest_signature": "sig7",
        }
        await client._handle_control(json.dumps(ctrl).encode(), ws)

        assert captured == [("/api/v1/agent/manifest.json", "sig7", "0.7.0")]


class TestUnknownControlTelemetry:
    async def test_unknown_type_increments_counter(self, client):
        ws = AsyncMock()
        # Two distinct unknown types — counter should reflect both.
        await client._handle_control(
            json.dumps({"type": "future.event"}).encode(), ws,
        )
        await client._handle_control(
            json.dumps({"type": "another_unknown"}).encode(), ws,
        )

        assert client._unknown_control_type_total == 2

    async def test_known_types_do_not_increment_counter(self, client):
        ws = AsyncMock()
        # ``ping`` is a known type — must not bump the unknown counter.
        await client._handle_control(
            json.dumps({"type": "ping"}).encode(), ws,
        )
        # ``mobile_session_detach`` has a no-op debug-log handler but is
        # explicitly recognized — also must not increment.
        await client._handle_control(
            json.dumps({"type": "mobile_session_detach", "session_id": "s"}).encode(),
            ws,
        )

        assert client._unknown_control_type_total == 0

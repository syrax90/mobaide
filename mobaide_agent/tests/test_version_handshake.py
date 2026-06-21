"""Cover ``GET /api/version`` on the agent and the ``server_info`` text frame
emitted by ``/ws/session/<id>``."""
from __future__ import annotations

import pytest

from mobaide import __version__
from mobaide.relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)


async def test_get_version(client) -> None:
    response = await client.get("/api/version")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "mobaide_agent"
    assert body["server_version"] == __version__
    assert body["protocol_versions"] == list(SUPPORTED_PROTOCOL_VERSIONS)
    assert body["min_client_version"] == MIN_CLIENT_PROTOCOL_VERSION
    assert body["latest_agent_version"] is None
    assert body["capabilities"] == {}
    # The signed manifest body now travels inline over the relay WebSocket
    # — it is not exposed via this REST surface.
    assert "manifest_url" not in body
    assert "manifest_signature" not in body


def test_protocol_version_constants() -> None:
    # proto-v5: additive bump adding the create-session ``cli`` discriminator
    # and ``codex_options`` for Codex CLI support (see PROTOCOL.md). This is a
    # deliberate tripwire — bumping the constants must consciously update it.
    assert PROTOCOL_VERSION == 5
    assert MIN_CLIENT_PROTOCOL_VERSION == 5
    assert tuple(SUPPORTED_PROTOCOL_VERSIONS) == (5,)

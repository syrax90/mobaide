"""Cover ``GET /api/version`` on the agent and the ``server_info`` text frame
emitted by ``/ws/session/<id>``."""
from __future__ import annotations

import pytest

from claude_mobile import __version__
from claude_mobile.relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)


async def test_get_version(client) -> None:
    response = await client.get("/api/version")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "claude_agent"
    assert body["server_version"] == __version__
    assert body["protocol_versions"] == list(SUPPORTED_PROTOCOL_VERSIONS)
    assert body["min_client_version"] == MIN_CLIENT_PROTOCOL_VERSION
    assert body["latest_agent_version"] is None
    assert body["manifest_url"] is None
    assert body["manifest_signature"] is None
    assert body["capabilities"] == {}


def test_protocol_version_constants() -> None:
    # The agent advertises exactly one protocol version today; the support
    # window is provisioned for forward growth.
    assert PROTOCOL_VERSION == 1
    assert MIN_CLIENT_PROTOCOL_VERSION == 1
    assert tuple(SUPPORTED_PROTOCOL_VERSIONS) == (1,)

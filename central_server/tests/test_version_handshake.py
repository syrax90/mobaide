"""Cover the ``/api/version`` endpoint and the manifest-aware auth_ok shape."""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient

from central_server import __version__
from central_server.app import create_app
from central_server.config import CentralServerConfig
from central_server.manifest.store import ManifestStore, canonical_json
from central_server.relay.envelope import (
    MIN_CLIENT_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
)
from tests.conftest import TEST_JWT_SECRET


def _signed_manifest(tmp_path: Path) -> tuple[Ed25519PrivateKey, str, dict, str]:
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    public_key_b64 = base64.b64encode(public_bytes).decode()
    body = {
        "version": "0.2.0",
        "tarball_url": "file:///tmp/agent.tar.gz",
        "tarball_sha256": "0" * 64,
        "wheel_url": "file:///tmp/agent.whl",
        "wheel_sha256": "0" * 64,
        "released_at": "2026-04-26T00:00:00+00:00",
    }
    signature = private_key.sign(canonical_json(body))
    signature_b64 = base64.b64encode(signature).decode()
    return private_key, public_key_b64, body, signature_b64


async def test_get_version_without_manifest(client) -> None:
    response = await client.get("/api/version")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "central_server"
    assert body["server_version"] == __version__
    assert body["protocol_versions"] == list(SUPPORTED_PROTOCOL_VERSIONS)
    assert body["min_client_version"] == MIN_CLIENT_PROTOCOL_VERSION
    assert body["latest_agent_version"] is None
    # proto-v2: the signed manifest body is delivered inline to the agent
    # over the relay WebSocket; it is not echoed back through ``/api/version``.
    assert "manifest_url" not in body
    assert "manifest_signature" not in body


async def test_get_version_reports_no_google_client_id_when_unconfigured(
    client,
) -> None:
    response = await client.get("/api/version")
    # Present-but-null, not absent: the SPA's bootstrap parses the key and
    # hides the Google button when it is null.
    assert response.json()["google_client_id"] is None


async def test_get_version_carries_the_configured_google_client_id(tmp_path) -> None:
    """The browser SPA is served as static files with no build-time config
    channel, so one anonymous probe has to cover the GIS client id too."""
    config = CentralServerConfig(
        jwt_secret_key=TEST_JWT_SECRET,
        db_path=str(tmp_path / "gis.db"),
        log_level="DEBUG",
        log_format="console",
        google_client_id="web-client.apps.googleusercontent.com",
        manifest_store_path=str(tmp_path / "manifest.json"),
    )
    application = create_app(config=config)
    application.state.manifest_store = ManifestStore(config.manifest_store_path)

    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="https://test") as anon:
        response = await anon.get("/api/version")

    assert response.status_code == 200
    assert (
        response.json()["google_client_id"]
        == "web-client.apps.googleusercontent.com"
    )


async def test_get_version_with_manifest(client, app, tmp_path) -> None:
    _, public_key_b64, body, signature_b64 = _signed_manifest(tmp_path)
    store = ManifestStore(tmp_path / "manifest.json")
    store.write(body, signature_b64, public_key_b64=public_key_b64)
    app.state.manifest_store = store

    response = await client.get("/api/version")
    assert response.status_code == 200
    payload = response.json()
    assert payload["latest_agent_version"] == "0.2.0"
    assert "manifest_url" not in payload
    assert "manifest_signature" not in payload


async def test_publish_manifest_requires_token(client) -> None:
    response = await client.post(
        "/api/internal/agent_manifest",
        json={"body": {}, "signature_b64": ""},
    )
    assert response.status_code in (401, 503)


async def test_publish_and_serve_manifest(client, app, config, tmp_path) -> None:
    _, public_key_b64, body, signature_b64 = _signed_manifest(tmp_path)
    object.__setattr__(config, "release_auth_token", "release-token-test")
    object.__setattr__(config, "manifest_signing_public_key_b64", public_key_b64)
    object.__setattr__(config, "manifest_store_path", str(tmp_path / "manifest.json"))
    app.state.config = config
    app.state.manifest_store = ManifestStore(config.manifest_store_path)

    response = await client.post(
        "/api/internal/agent_manifest",
        headers={"X-Release-Token": "release-token-test"},
        json={"body": body, "signature_b64": signature_b64},
    )
    assert response.status_code == 200, response.text

    follow_up = await client.get("/api/v1/agent/manifest.json")
    assert follow_up.status_code == 200
    record = follow_up.json()
    assert record["body"]["version"] == "0.2.0"
    assert record["signature_b64"] == signature_b64


async def test_publish_manifest_rejects_bad_signature(client, app, config, tmp_path) -> None:
    _, public_key_b64, body, _ = _signed_manifest(tmp_path)
    object.__setattr__(config, "release_auth_token", "release-token-test")
    object.__setattr__(config, "manifest_signing_public_key_b64", public_key_b64)
    object.__setattr__(config, "manifest_store_path", str(tmp_path / "manifest.json"))
    app.state.config = config
    app.state.manifest_store = ManifestStore(config.manifest_store_path)

    response = await client.post(
        "/api/internal/agent_manifest",
        headers={"X-Release-Token": "release-token-test"},
        json={"body": body, "signature_b64": base64.b64encode(b"\x00" * 64).decode()},
    )
    assert response.status_code == 400

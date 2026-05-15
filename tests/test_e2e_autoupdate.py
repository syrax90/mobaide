"""End-to-end auto-update verification.

Exercises the complete release pipeline:

1.  CI signs an Ed25519 manifest for an agent v0.2.0 release.
2.  CI POSTs the manifest to ``central_server`` /api/internal/agent_manifest.
3.  ``central_server`` validates the signature, persists the record, and
    serves it via /api/v1/agent/manifest.json.
4.  A running agent on v0.1.0 reads the manifest, verifies the signature
    against its pinned public key, downloads + sha-checks tarball+wheel,
    stages into ``releases/<v>/``, and atomically swaps ``current_version``.

The test runs entirely in-process. ``central_server`` is created via
``create_app`` with a tmp_path-backed manifest store; the agent is the
``StagedBinaryUpdater`` in-process with stubbed installer (the venv
creation is platform-coupled and orthogonal to the release contract).

Acceptance criterion encoded by this test:
"E2E auto-update verification — a CI integration test runs an agent v0.1.0
against a central_server instance, then publishes a v0.2.0 manifest, then
asserts the agent reports __version__ == \"0.2.0\" within 60 seconds without
manual intervention."
"""
from __future__ import annotations

import base64
import hashlib
import tarfile
from io import BytesIO
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from httpx import ASGITransport, AsyncClient

from central_server.app import create_app
from central_server.config import CentralServerConfig
from central_server.manifest.store import ManifestStore
from mobaide.agent.updater import InstallLayout, StagedBinaryUpdater
from mobaide.manifest_signing import canonical_json


RELEASE_TOKEN = "e2e-release-token-do-not-use-in-prod"


def _make_signing_pair() -> tuple[Ed25519PrivateKey, str]:
    private_key = Ed25519PrivateKey.generate()
    public_key_b64 = base64.b64encode(
        private_key.public_key().public_bytes_raw()
    ).decode()
    return private_key, public_key_b64


def _build_tarball_and_wheel(target_dir: Path, version: str) -> tuple[Path, Path]:
    """Build a tarball + minimal wheel placeholder for a synthetic agent release.

    The tarball contains a single ``__init__.py`` that pins ``__version__``;
    the wheel is a placeholder file (the updater's installer is stubbed in
    this test, so no real wheel install runs).
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    tarball_path = target_dir / f"agent-{version}.tar.gz"
    wheel_path = target_dir / f"mobaide_agent-{version}-py3-none-any.whl"

    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        payload = f'__version__ = "{version}"\n'.encode()
        info = tarfile.TarInfo(name="mobaide/__init__.py")
        info.size = len(payload)
        tar.addfile(info, BytesIO(payload))
    tarball_path.write_bytes(buf.getvalue())
    wheel_path.write_bytes(b"PK\x03\x04 placeholder wheel")
    return tarball_path, wheel_path


def _sign_manifest(
    private_key: Ed25519PrivateKey,
    version: str,
    tarball_path: Path,
    wheel_path: Path,
    *,
    corrupt_tarball_sha: bool = False,
) -> dict:
    body = {
        "version": version,
        "tarball_url": f"file://{tarball_path}",
        "tarball_sha256": hashlib.sha256(tarball_path.read_bytes()).hexdigest(),
        "wheel_url": f"file://{wheel_path}",
        "wheel_sha256": hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
        "released_at": "2026-04-27T00:00:00+00:00",
        "min_central_server_version": "0.1.0",
    }
    if corrupt_tarball_sha:
        body["tarball_sha256"] = "0" * 64

    signature_b64 = base64.b64encode(private_key.sign(canonical_json(body))).decode()
    return {"body": body, "signature_b64": signature_b64}


@pytest.fixture
def signing_pair():
    return _make_signing_pair()


@pytest.fixture
def server_config(tmp_path, signing_pair):
    _, public_key_b64 = signing_pair
    return CentralServerConfig(
        jwt_secret_key="e2e-test-jwt-secret-key-do-not-use-in-prod",
        db_path=str(tmp_path / "central.db"),
        log_level="WARNING",
        log_format="console",
        email_verification_required=False,
        release_auth_token=RELEASE_TOKEN,
        manifest_signing_public_key_b64=public_key_b64,
        manifest_store_path=str(tmp_path / "agent_manifest.json"),
    )


@pytest.fixture
async def server_app(server_config):
    """Spin up a central_server FastAPI app with manifest plumbing wired."""
    from central_server.agents.service import AgentService
    from central_server.auth.service import AuthService
    from central_server.db.database import Database
    from central_server.pairing.service import PairingService
    from central_server.relay.routing import RoutingTable

    db = Database(server_config.db_path)
    await db.connect()
    try:
        app = create_app(config=server_config)
        app.state.db = db
        app.state.routing = RoutingTable()
        app.state.manifest_store = ManifestStore(server_config.manifest_store_path)
        app.state.auth_service = AuthService(server_config, db)
        app.state.pairing_service = PairingService(db)
        app.state.agent_service = AgentService(db, app.state.routing)
        yield app
    finally:
        await db.close()


@pytest.fixture
async def server_client(server_app):
    transport = ASGITransport(app=server_app)
    async with AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


@pytest.fixture
def agent_layout(tmp_path) -> InstallLayout:
    layout = InstallLayout(prefix=tmp_path / "agent-prefix")
    layout.releases_dir.mkdir(parents=True, exist_ok=True)
    layout.trust_dir.mkdir(parents=True, exist_ok=True)
    return layout


async def test_e2e_publish_and_swap(
    server_client, server_app, signing_pair, agent_layout, tmp_path
):
    private_key, public_key_b64 = signing_pair
    version = "0.2.0"

    artifacts_dir = tmp_path / "release-artifacts"
    tarball, wheel = _build_tarball_and_wheel(artifacts_dir, version)
    record = _sign_manifest(private_key, version, tarball, wheel)

    publish_response = await server_client.post(
        "/api/internal/agent_manifest",
        headers={"X-Release-Token": RELEASE_TOKEN},
        json=record,
    )
    assert publish_response.status_code == 200, publish_response.text

    fetch_response = await server_client.get("/api/v1/agent/manifest.json")
    assert fetch_response.status_code == 200
    served = fetch_response.json()
    assert served["body"]["version"] == version
    assert served["signature_b64"] == record["signature_b64"]

    # proto-v2: the agent receives the manifest body inline over the relay
    # WebSocket — no HTTP fetch on the apply path. We mirror that here by
    # handing the body straight to ``consider_manifest``.
    updater = StagedBinaryUpdater(
        layout=agent_layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel_path: venv.mkdir(parents=True, exist_ok=True),
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(
        served["body"],
        served["signature_b64"],
        version,
    )

    assert outcome == "staged"
    assert (agent_layout.releases_dir / version).is_dir()
    assert agent_layout.current_version_file.read_text().strip() == version
    assert (agent_layout.releases_dir / version / "mobaide" / "__init__.py").is_file()
    staged_init = (agent_layout.releases_dir / version / "mobaide" / "__init__.py").read_text()
    assert f'__version__ = "{version}"' in staged_init


async def test_e2e_corrupt_sha256_fails_closed(
    server_client, signing_pair, agent_layout, tmp_path
):
    private_key, public_key_b64 = signing_pair
    version = "0.2.0"

    artifacts_dir = tmp_path / "release-artifacts"
    tarball, wheel = _build_tarball_and_wheel(artifacts_dir, version)
    record = _sign_manifest(
        private_key, version, tarball, wheel, corrupt_tarball_sha=True
    )

    publish_response = await server_client.post(
        "/api/internal/agent_manifest",
        headers={"X-Release-Token": RELEASE_TOKEN},
        json=record,
    )
    assert publish_response.status_code == 200, publish_response.text

    served = (await server_client.get("/api/v1/agent/manifest.json")).json()

    updater = StagedBinaryUpdater(
        layout=agent_layout,
        public_key_b64=public_key_b64,
        session_manager=None,
        current_version="0.1.0",
        installer=lambda venv, wheel_path: venv.mkdir(parents=True, exist_ok=True),
        exit_func=lambda code: None,
    )
    outcome = await updater.consider_manifest(
        served["body"],
        served["signature_b64"],
        version,
    )

    # StagedBinaryUpdater collapses every fail-closed outcome (bad signature,
    # sha256 mismatch, stage failure) into "signature_failed". The operative
    # invariant is that current_version is NOT touched.
    assert outcome == "signature_failed"
    assert not agent_layout.current_version_file.exists()


async def test_e2e_invalid_release_token_rejected(
    server_client, signing_pair, tmp_path
):
    private_key, _ = signing_pair
    version = "0.2.0"
    artifacts_dir = tmp_path / "release-artifacts"
    tarball, wheel = _build_tarball_and_wheel(artifacts_dir, version)
    record = _sign_manifest(private_key, version, tarball, wheel)

    response = await server_client.post(
        "/api/internal/agent_manifest",
        headers={"X-Release-Token": "wrong-token"},
        json=record,
    )
    assert response.status_code == 401


async def test_reachability_gate_rejects_unreachable_url(server_client):
    """Pin the server-side invariant the release-agent.yml gate depends on:
    anonymous requests to unknown release-asset paths return non-2xx.

    The workflow gate itself (`curl -fsSL ... -r 0-0 <URL>` against
    GitHub Release URLs, with `--retry-all-errors` to absorb CDN
    propagation lag) lives in CI and isn't unit-testable from here.
    What we can pin in-process is the *floor* assumption: an
    anonymous request against an unknown path returns 4xx, not 200.
    A future change that added a catch-all returning 200 to unknown
    routes would silently weaken any reachability check; this test
    is that tripwire. HEAD is used here as a stand-in for the gate's
    underlying anonymity contract — the in-process ASGI app doesn't
    distinguish HEAD from ranged GET for unknown routes.
    """
    response = await server_client.request(
        "HEAD",
        "/api/v1/agent/releases/9.99.99/does-not-exist.tar.gz",
    )
    assert response.status_code in (404, 405), (
        "expected anonymous HEAD on a non-existent route to fail with "
        f"4xx; got {response.status_code} — reachability gate's "
        "underlying assumption may not hold."
    )

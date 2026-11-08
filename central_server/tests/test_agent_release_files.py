"""Cover agent release file hosting: the token-guarded PUT and the public GET."""
from __future__ import annotations

import hashlib
import os

import pytest

from central_server.manifest import release_files
from central_server.manifest.release_files import ReleaseFileError, ReleaseFileStore

TOKEN = "release-token-test"
BODY = b"mobaide agent tarball bytes"
SHA = hashlib.sha256(BODY).hexdigest()
NAME = "mobaide-agent-0.3.24.tar.gz"


@pytest.fixture
def release_app(app):
    app.state.config = type(app.state.config)(
        **{**app.state.config.__dict__, "release_auth_token": TOKEN}
    )
    return app


async def _put(client, sha=SHA, name=NAME, body=BODY, token=TOKEN):
    headers = {"Content-Type": "application/octet-stream"}
    if token is not None:
        headers["X-Release-Token"] = token
    return await client.put(
        f"/api/internal/agent_release/{sha}/{name}", content=body, headers=headers
    )


@pytest.mark.asyncio
async def test_upload_then_anonymous_download(client, release_app):
    resp = await _put(client)
    assert resp.status_code == 200
    assert resp.json() == {"sha256": SHA, "filename": NAME, "created": True}

    got = await client.get(f"/api/v1/agent/releases/{SHA}/{NAME}")
    assert got.status_code == 200
    assert got.content == BODY
    assert got.headers["content-type"] == "application/octet-stream"
    assert "immutable" in got.headers["cache-control"]

    head = await client.head(f"/api/v1/agent/releases/{SHA}/{NAME}")
    assert head.status_code == 200
    assert head.headers["content-length"] == str(len(BODY))


@pytest.mark.asyncio
async def test_reupload_is_idempotent(client, release_app):
    assert (await _put(client)).json()["created"] is True
    again = await _put(client)
    assert again.status_code == 200
    assert again.json()["created"] is False


@pytest.mark.asyncio
async def test_ranged_get(client, release_app):
    await _put(client)
    got = await client.get(
        f"/api/v1/agent/releases/{SHA}/{NAME}", headers={"Range": "bytes=0-0"}
    )
    assert got.status_code == 206
    assert got.content == BODY[:1]


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "wrong"])
async def test_upload_requires_token(client, release_app, token):
    resp = await _put(client, token=token)
    assert resp.status_code == 401
    missing = await client.get(f"/api/v1/agent/releases/{SHA}/{NAME}")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_upload_503_without_configured_token(client, app):
    resp = await _put(client)
    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_upload_rejects_digest_mismatch(client, release_app):
    resp = await _put(client, body=b"other bytes")
    assert resp.status_code == 400
    assert "sha256 mismatch" in resp.json()["detail"]
    missing = await client.get(f"/api/v1/agent/releases/{SHA}/{NAME}")
    assert missing.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "sha,name",
    [
        (SHA.upper(), NAME),
        (SHA[:-1], NAME),
        (SHA, ".hidden"),
        (SHA, "a%5Cb.whl"),
    ],
)
async def test_upload_rejects_bad_keys(client, release_app, sha, name):
    resp = await _put(client, sha=sha, name=name)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_rejects_oversize(client, release_app, monkeypatch):
    monkeypatch.setattr("central_server.manifest.router.MAX_RELEASE_FILE_BYTES", 4)
    resp = await _put(client)
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_missing_file_is_not_cacheable(client, release_app):
    resp = await client.get(f"/api/v1/agent/releases/{SHA}/{NAME}")
    assert resp.status_code == 404
    assert resp.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_bad_key_get_is_404(client, release_app):
    resp = await client.get(f"/api/v1/agent/releases/{SHA}/.hidden")
    assert resp.status_code == 404


def _blob(i: int) -> tuple[str, bytes]:
    data = f"release {i}".encode()
    return hashlib.sha256(data).hexdigest(), data


def _seed(store, tmp_path, blobs, name="f.whl"):
    """Save blobs with strictly increasing mtimes, oldest first."""
    for i, (sha, data) in enumerate(blobs):
        store.save(sha, name, data)
        os.utime(tmp_path / sha / name, (1000 + i, 1000 + i))


def test_prune_keeps_newest_and_protected(tmp_path, monkeypatch):
    store = ReleaseFileStore(tmp_path)
    blobs = [_blob(i) for i in range(5)]
    _seed(store, tmp_path, blobs[:4])
    monkeypatch.setattr(release_files, "KEEP_RELEASE_FILES", 3)

    oldest = blobs[0][0]
    second = blobs[1][0]
    store.save(blobs[4][0], "f.whl", blobs[4][1], protected={oldest})

    # The cap counts protected files too: oldest (protected), the new upload,
    # and the newest remaining file make three.
    kept = sorted(p.parent.name for p in tmp_path.glob("*/f.whl"))
    assert kept == sorted([oldest, blobs[3][0], blobs[4][0]])
    assert not (tmp_path / second).exists()


def test_prune_enforces_total_byte_budget(tmp_path, monkeypatch):
    store = ReleaseFileStore(tmp_path)
    blobs = [
        (hashlib.sha256(data).hexdigest(), data)
        for data in (bytes([i]) * 100 for i in range(4))
    ]
    _seed(store, tmp_path, blobs[:3])
    monkeypatch.setattr(release_files, "MAX_RELEASE_TOTAL_BYTES", 250)

    store.save(blobs[3][0], "f.whl", blobs[3][1])

    kept = sorted(p.parent.name for p in tmp_path.glob("*/f.whl"))
    assert kept == sorted([blobs[2][0], blobs[3][0]])
    assert sum(p.stat().st_size for p in tmp_path.rglob("*") if p.is_file()) <= 250


def test_prune_counts_every_name_under_one_digest(tmp_path, monkeypatch):
    store = ReleaseFileStore(tmp_path)
    sha, data = _blob(0)
    for i in range(5):
        store.save(sha, f"name{i}.whl", data)
        os.utime(tmp_path / sha / f"name{i}.whl", (1000 + i, 1000 + i))
    monkeypatch.setattr(release_files, "KEEP_RELEASE_FILES", 2)

    other_sha, other = _blob(1)
    store.save(other_sha, "f.whl", other)

    assert sorted(p.name for p in (tmp_path / sha).iterdir()) == ["name4.whl"]


def test_prune_drops_stale_temp_files_only(tmp_path):
    store = ReleaseFileStore(tmp_path)
    sha, data = _blob(0)
    store.save(sha, "f.whl", data)
    stale = tmp_path / sha / ".upload-stale"
    fresh = tmp_path / sha / ".upload-fresh"
    stale.write_bytes(b"x" * 10)
    fresh.write_bytes(b"x" * 10)
    os.utime(stale, (1000, 1000))

    other_sha, other = _blob(1)
    store.save(other_sha, "f.whl", other)

    assert not stale.exists()
    assert fresh.exists()


def test_save_refuses_when_disk_would_run_low(tmp_path, monkeypatch):
    store = ReleaseFileStore(tmp_path)
    free = release_files.shutil.disk_usage(tmp_path).free
    monkeypatch.setattr(release_files, "MIN_FREE_BYTES", free + 1)
    sha, data = _blob(0)

    with pytest.raises(release_files.ReleaseStorageFullError):
        store.save(sha, "f.whl", data)
    assert not (tmp_path / sha).exists()


@pytest.mark.asyncio
async def test_upload_507_when_disk_would_run_low(client, release_app, monkeypatch):
    monkeypatch.setattr(release_files, "MIN_FREE_BYTES", 1 << 62)
    resp = await _put(client)
    assert resp.status_code == 507


def test_save_rejects_mismatch_without_writing(tmp_path):
    store = ReleaseFileStore(tmp_path)
    with pytest.raises(ReleaseFileError):
        store.save(SHA, NAME, b"nope")
    assert not (tmp_path / SHA).exists()

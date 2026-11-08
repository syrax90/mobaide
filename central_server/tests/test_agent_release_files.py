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


def test_prune_keeps_newest_and_protected(tmp_path, monkeypatch):
    store = ReleaseFileStore(tmp_path)
    blobs = [_blob(i) for i in range(5)]
    for i, (sha, data) in enumerate(blobs[:4]):
        store.save(sha, "f.whl", data)
        os.utime(tmp_path / sha, (1000 + i, 1000 + i))
    monkeypatch.setattr(release_files, "KEEP_RELEASE_FILES", 3)

    oldest = blobs[0][0]
    second = blobs[1][0]
    store.save(blobs[4][0], "f.whl", blobs[4][1], protected={oldest})

    assert store.path_for(oldest, "f.whl") is not None
    assert store.path_for(second, "f.whl") is None
    for sha, _ in blobs[2:]:
        assert store.path_for(sha, "f.whl") is not None


def test_save_rejects_mismatch_without_writing(tmp_path):
    store = ReleaseFileStore(tmp_path)
    with pytest.raises(ReleaseFileError):
        store.save(SHA, NAME, b"nope")
    assert not (tmp_path / SHA).exists()
